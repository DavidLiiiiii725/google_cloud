"""MongoDB MCP bridge.

This is the Phase-4 judging hook: instead of the backend calling pymongo directly,
**Gemini** decides what to do and calls tools exposed by the official
``mongodb-mcp-server`` (a Node MCP server). The MCP server performs the real
MongoDB I/O. So a request flows:

    natural language  ->  Gemini (Vertex AI, ADC)
                      ->  function call  ->  this bridge
                      ->  MCP stdio  ->  mongodb-mcp-server
                      ->  real mongod  ->  result back up the chain.

Design notes
------------
* The MCP stdio client is anyio/asyncio based and its session must live inside a
  single event loop for its whole lifetime. FastAPI already owns an event loop and
  we want the session to survive across many requests, so we run the MCP session in
  a **dedicated background thread with its own asyncio loop**. Everything else (the
  Gemini SDK, FastAPI handlers) talks to it through thread-safe synchronous methods
  that marshal coroutines onto that loop via ``run_coroutine_threadsafe``.

* google-genai 0.3.0 has no ``parameters_json_schema`` field, so we convert each
  MCP tool's JSON-Schema into ``types.Schema`` ourselves. Vertex's function-calling
  schema dialect rejects free-form objects (an ``object`` with no declared
  properties — exactly what a Mongo ``filter`` / ``document`` is), so those are
  exposed to Gemini as STRING ("a JSON object string") and re-hydrated with
  ``json.loads`` before being handed to the MCP server.

Never raises on the hot path: if the MCP server or Gemini is unavailable the bridge
reports the failure in its trace and returns a structured error instead of crashing.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import asyncio
from typing import Any, Optional

from dotenv import load_dotenv
from pathlib import Path

# Load the shared .env (same file the rest of the app uses).
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

MONGODB_URI = os.getenv("MDB_MCP_CONNECTION_STRING") or os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017/?directConnection=true")
print(f"[mcp_mongo] MONGODB_URI starts with: {MONGODB_URI[:25]!r}", flush=True)
MONGODB_DB = os.getenv("MONGODB_DB", "agent_greenhouse")
GCP_PROJECT = os.getenv("GCP_PROJECT", "")
GCP_REGION = os.getenv("GCP_REGION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


# --------------------------------------------------------------------------- #
# JSON-Schema  ->  google-genai types.Schema
# --------------------------------------------------------------------------- #
_TYPE_MAP = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def _json_schema_to_gemini(schema: dict, _depth: int = 0):
    """Convert one JSON-Schema node into a google-genai types.Schema.

    Free-form objects (object with no declared properties) and anything too deep
    or exotic collapse to STRING carrying a "pass JSON here" hint — the bridge
    re-hydrates those with json.loads before the MCP call.
    """
    from google.genai import types as t

    if not isinstance(schema, dict):
        return t.Schema(type="STRING")

    # anyOf/oneOf/allOf — pick the first concrete branch we understand.
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema and isinstance(schema[key], list):
            for branch in schema[key]:
                if isinstance(branch, dict) and branch.get("type") != "null":
                    merged = {k: v for k, v in schema.items() if k not in ("anyOf", "oneOf", "allOf")}
                    merged.update(branch)
                    return _json_schema_to_gemini(merged, _depth)

    jtype = schema.get("type")
    nullable = False
    if isinstance(jtype, list):
        non_null = [x for x in jtype if x != "null"]
        nullable = "null" in jtype
        jtype = non_null[0] if non_null else "string"

    desc = (schema.get("description") or "")[:900]
    gtype = _TYPE_MAP.get(jtype, "STRING")

    # Free-form object or too-deep nesting -> STRING the model fills with JSON.
    props = schema.get("properties") or {}
    if gtype == "OBJECT" and (not props or _depth >= 4):
        hint = "A JSON object provided as a string, e.g. '{\"field\": \"value\"}'."
        return t.Schema(type="STRING", description=(desc + " " + hint).strip(), nullable=nullable or None)

    kwargs: dict[str, Any] = {"type": gtype}
    if desc:
        kwargs["description"] = desc
    if nullable:
        kwargs["nullable"] = True
    if schema.get("enum"):
        kwargs["enum"] = [str(e) for e in schema["enum"]]

    if gtype == "OBJECT":
        kwargs["properties"] = {k: _json_schema_to_gemini(v, _depth + 1) for k, v in props.items()}
        req = [r for r in (schema.get("required") or []) if r in props]
        if req:
            kwargs["required"] = req
    elif gtype == "ARRAY":
        items = schema.get("items")
        kwargs["items"] = _json_schema_to_gemini(items if isinstance(items, dict) else {"type": "string"}, _depth + 1)

    return t.Schema(**kwargs)


def _maybe_json(val):
    """If val is a JSON-object/array string, parse it; otherwise return unchanged."""
    if isinstance(val, str):
        s = val.strip()
        if s[:1] in ("{", "["):
            try:
                return json.loads(s)
            except (json.JSONDecodeError, ValueError):
                return val
    return val


def _coerce_args(mcp_schema: dict, args: dict) -> dict:
    """Re-hydrate args before sending to MCP. Vertex function-calling can't express
    free-form objects, so the bridge exposes them as STRING and Gemini fills them with
    JSON text. Here we turn that text back into real objects/arrays the way the MCP
    server expects — including the common ``documents: [ "{...}", "{...}" ]`` shape where
    each array element arrived as a JSON string."""
    if not isinstance(args, dict) or not isinstance(mcp_schema, dict):
        return args
    props = mcp_schema.get("properties") or {}
    out = dict(args)
    for key, val in list(out.items()):
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue
        decl = spec.get("type")
        decl_set = set(decl) if isinstance(decl, list) else {decl}

        if "array" in decl_set:
            # Whole array sent as a string, or individual elements sent as strings.
            val = _maybe_json(val)
            if isinstance(val, list):
                item_spec = spec.get("items") or {}
                item_types = item_spec.get("type")
                item_set = set(item_types) if isinstance(item_types, list) else {item_types}
                if item_set & {"object", "array"}:
                    val = [_maybe_json(el) for el in val]
            out[key] = val
        elif "object" in decl_set:
            val = _maybe_json(val)
            if isinstance(val, dict):
                val = _coerce_args(spec, val)
            out[key] = val
    return out


# --------------------------------------------------------------------------- #
# The bridge — persistent MCP session on a dedicated thread + loop.
# --------------------------------------------------------------------------- #
class MongoMCPBridge:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session = None
        self._tools: list = []                 # raw MCP tool objects
        self._schemas: dict[str, dict] = {}    # tool name -> inputSchema dict
        self._ready = threading.Event()
        self._start_error: Optional[str] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._started = False
        self._lock = threading.Lock()

    # ---- lifecycle -------------------------------------------------------- #
    def start(self, timeout: float = 45.0) -> bool:
        """Idempotently spawn the MCP server and initialize the session.
        Returns True if connected and tools were listed."""
        with self._lock:
            if self._ready.is_set():
                return True
            if self._started:
                pass  # a start is already in flight; fall through to wait
            else:
                self._started = True
                self._thread = threading.Thread(target=self._thread_main, name="mongo-mcp", daemon=True)
                self._thread.start()
        ok = self._ready.wait(timeout)
        if not ok and self._start_error is None:
            self._start_error = f"MCP server did not become ready within {timeout}s"
        return ok and self._start_error is None

    def _thread_main(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._runner())
        except Exception as e:  # pragma: no cover - defensive
            self._start_error = f"{type(e).__name__}: {e}"
            self._ready.set()

    async def _runner(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        npx = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
        params = StdioServerParameters(
            command=npx,
            args=["-y", "mongodb-mcp-server@latest"],
            env={**os.environ, "MDB_MCP_CONNECTION_STRING": MONGODB_URI},
        )
        self._stop_event = asyncio.Event()
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self._tools = list(listing.tools)
                    self._schemas = {tl.name: (tl.inputSchema or {}) for tl in self._tools}
                    self._session = session
                    self._ready.set()
                    await self._stop_event.wait()
        except Exception as e:
            self._start_error = f"{type(e).__name__}: {e}"
            self._ready.set()

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    # ---- introspection ---------------------------------------------------- #
    def is_ready(self) -> bool:
        return self._ready.is_set() and self._start_error is None and self._session is not None

    @property
    def error(self) -> Optional[str]:
        return self._start_error

    def tool_names(self) -> list[str]:
        return [t.name for t in self._tools]

    def tool_summaries(self) -> list[dict]:
        out = []
        for t in self._tools:
            out.append({
                "name": t.name,
                "description": (t.description or "")[:200],
                "params": list((t.inputSchema or {}).get("properties", {}).keys()),
            })
        return out

    # ---- raw MCP tool call ------------------------------------------------ #
    def call_tool(self, name: str, args: dict, timeout: float = 30.0) -> dict:
        """Synchronously call an MCP tool against the real mongod. Returns a
        structured dict: {ok, text, structured, error}."""
        if not self.is_ready():
            return {"ok": False, "error": self._start_error or "MCP bridge not ready", "text": ""}
        args = _coerce_args(self._schemas.get(name, {}), args or {})
        fut = asyncio.run_coroutine_threadsafe(self._session.call_tool(name, args), self._loop)  # type: ignore[arg-type]
        try:
            result = fut.result(timeout)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "text": ""}
        # Flatten MCP CallToolResult -> text + any structured content.
        texts: list[str] = []
        for block in getattr(result, "content", []) or []:
            if getattr(block, "type", None) == "text" or hasattr(block, "text"):
                texts.append(getattr(block, "text", "") or "")
        structured = getattr(result, "structuredContent", None)
        is_err = bool(getattr(result, "isError", False))
        return {
            "ok": not is_err,
            "text": "\n".join(t for t in texts if t),
            "structured": structured,
            "error": ("\n".join(texts) if is_err else None),
        }

    # ---- Gemini function-calling loop ------------------------------------- #
    def _gemini_client(self):
        from google import genai
        return genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_REGION)

    def _function_declarations(self, allow: Optional[set[str]] = None):
        from google.genai import types as t
        decls = []
        for tool in self._tools:
            if allow is not None and tool.name not in allow:
                continue
            schema = tool.inputSchema or {}
            props = schema.get("properties") or {}
            params = _json_schema_to_gemini(schema) if props else None
            decls.append(t.FunctionDeclaration(
                name=tool.name,
                description=(tool.description or "")[:900],
                parameters=params,
            ))
        return decls

    def gemini_query(self, request: str, *, system: Optional[str] = None,
                     allow_tools: Optional[list[str]] = None, max_turns: int = 8) -> dict:
        """Run a real Gemini function-calling loop over the MCP tools.

        Gemini reads the request, calls MongoDB MCP tools as needed (each executed
        against the real mongod), then produces a final natural-language answer.
        Returns {answer, trace, model, ok}.
        """
        trace: list[dict] = []
        if not self.is_ready():
            return {"ok": False, "answer": "", "trace": trace,
                    "error": self._start_error or "MCP bridge not ready", "model": GEMINI_MODEL}

        from google.genai import types as t

        try:
            client = self._gemini_client()
        except Exception as e:
            return {"ok": False, "answer": "", "trace": trace,
                    "error": f"Gemini init failed: {type(e).__name__}: {e}", "model": GEMINI_MODEL}

        allow = set(allow_tools) if allow_tools else None
        tools = [t.Tool(function_declarations=self._function_declarations(allow))]
        sys_inst = system or (
            f"You are a data agent for the Agent Greenhouse system. You operate on a MongoDB "
            f"database named '{MONGODB_DB}' using the provided MongoDB tools. When a tool needs a "
            f"filter/document/update/pipeline, pass it as a compact JSON string. Always target the "
            f"'{MONGODB_DB}' database. After gathering what you need, answer the user concisely and "
            f"state the concrete results (counts, ids, values) you obtained from the database."
        )
        config = t.GenerateContentConfig(
            tools=tools,
            temperature=0,
            system_instruction=sys_inst,
        )
        contents = [t.Content(role="user", parts=[t.Part.from_text(text=request)])]

        for _turn in range(max_turns):
            try:
                resp = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
            except Exception as e:
                return {"ok": False, "answer": "", "trace": trace,
                        "error": f"Gemini call failed: {type(e).__name__}: {e}", "model": GEMINI_MODEL}

            cand = (resp.candidates or [None])[0]
            if cand is None or cand.content is None:
                break
            parts = cand.content.parts or []
            fcalls = [p.function_call for p in parts if getattr(p, "function_call", None)]

            if not fcalls:
                answer = (resp.text or "").strip()
                return {"ok": True, "answer": answer, "trace": trace, "model": GEMINI_MODEL}

            contents.append(cand.content)  # record the model's tool-call turn
            response_parts = []
            for fc in fcalls:
                args = dict(fc.args or {})
                result = self.call_tool(fc.name, args)
                trace.append({"tool": fc.name, "args": args,
                              "ok": result.get("ok"),
                              "result": (result.get("text") or "")[:1200],
                              "error": result.get("error")})
                response_parts.append(t.Part.from_function_response(
                    name=fc.name,
                    response={"result": result.get("text", ""),
                              "structured": result.get("structured"),
                              "ok": result.get("ok"),
                              "error": result.get("error")},
                ))
            contents.append(t.Content(role="user", parts=response_parts))

        return {"ok": True, "answer": "(stopped: reached max tool turns)", "trace": trace, "model": GEMINI_MODEL}


# Process-wide singleton.
bridge = MongoMCPBridge()
