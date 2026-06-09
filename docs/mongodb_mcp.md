# MongoDB MCP — Gemini calls MongoDB through the MCP server

This is the Phase-4 "judging hook": the agent doesn't touch the database with a
hand-written `pymongo` call. Instead **Gemini decides what to do and calls tools
exposed by the official `mongodb-mcp-server`**, which performs the real MongoDB I/O.

```
natural-language request
        │
        ▼
   Gemini 2.5 Flash  (Vertex AI, ADC)         ← real model, real reasoning
        │  function_call: find / insert-many / aggregate / count / …
        ▼
   app/mcp_mongo.py  (the bridge)             ← converts schemas, runs the loop
        │  MCP stdio (JSON-RPC)
        ▼
   mongodb-mcp-server  (Node, via npx)        ← 29 official MongoDB MCP tools
        │  MongoDB wire protocol
        ▼
   real mongod 8.x on 127.0.0.1:27017         ← real database
```

Every operation in the loop runs against the real database; nothing is mocked.

## Why a local mongod (not Atlas)

MongoDB Atlas is **TLS-blocked on this network**. The TCP connection completes but
the TLS handshake is killed with a server-side `TLSV1_ALERT_INTERNAL_ERROR` — and
Python's OpenSSL *and* Node's TLS stack fail identically, which rules out a cert or
driver bug. This is GFW interference on the path to Atlas. The MongoDB binary CDN
(`fastdl.mongodb.org`) is reachable, so the system runs against a real local mongod
instead. Same wire protocol, same driver, same MCP server — just local.

To switch back to Atlas (e.g. behind a VPN), point `MONGODB_URI` at the
`mongodb+srv://…` cluster; nothing else changes.

## Setup

```powershell
# one-time: download + extract a real mongod under .localdb\
powershell -ExecutionPolicy Bypass -File scripts\install-mongo.ps1

# every run: started automatically by run.ps1 / run.bat (idempotent)
powershell -ExecutionPolicy Bypass -File scripts\start-mongo.ps1
```

Requirements: **Node/npx on PATH** (for `mongodb-mcp-server`) and **ADC** logged in
(`gcloud auth application-default login`) for Vertex Gemini. `pip install -r
requirements.txt` brings in the `mcp` Python SDK.

## How it works (`app/mcp_mongo.py`)

* **Persistent session on a dedicated thread.** The MCP stdio client is asyncio/anyio
  based and its session must live in one event loop for its whole lifetime. FastAPI
  owns its own loop, so the bridge runs the MCP session in a dedicated background
  thread with its own loop and exposes thread-safe synchronous methods
  (`call_tool`, `gemini_query`) that marshal coroutines onto it.

* **Schema translation.** google-genai 0.3.0 has no `parameters_json_schema`, so each
  MCP tool's JSON-Schema is converted to `types.Schema`. Vertex's function-calling
  dialect can't express a *free-form object* (an `object` with no declared
  properties — exactly what a Mongo `filter`/`document`/`pipeline` is), so those are
  exposed to Gemini as `STRING` ("pass JSON here") and **re-hydrated** with
  `json.loads` before the MCP call — including the `documents: ["{…}", "{…}"]` shape
  where each array element arrives as a JSON string.

* **Never crashes the demo.** If the MCP server or Gemini is unavailable the bridge
  reports it in the trace / `error` and returns a structured result instead of
  raising.

## API

| Endpoint | What it does |
|---|---|
| `GET /api/mcp/status` | Bridge ready state + the list of MCP tools discovered |
| `POST /api/mcp/query` | `{"request": "...", "allow_tools": [...]?}` → runs the real Gemini↔MCP loop, returns `{answer, trace, model, ok}` |

The `trace` lists every MongoDB tool call Gemini made, with args and results — so you
can show judges exactly which real database operations the model chose.

### Example

```bash
curl -s -X POST http://127.0.0.1:8000/api/mcp/query -H "Content-Type: application/json" \
  -d '{"request":"How many documents are in live_telemetry? Find the most recent one and tell me its temperature, humidity and severity."}'
```

```
trace:  count(live_telemetry)         -> Found N documents
        find(sort {ts:-1} limit 1)    -> 1 document
answer: The most recent live_telemetry document has a temperature of 24,
        humidity of 65, and a severity of "nominal".
```

## Smoke test

```powershell
.venv\Scripts\python.exe scripts\mcp_smoke.py
```

Starts the bridge, lists tools, does a direct MCP insert/count, then runs a
Gemini-driven query — printing the tool trace and the model's answer.
