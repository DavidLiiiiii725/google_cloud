"""Smoke test: real Gemini -> MongoDB MCP -> real mongod.

Run with the project venv after mongod is listening on 27017:
    .venv\\Scripts\\python.exe scripts\\mcp_smoke.py
"""
import sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import mcp_mongo


def main() -> int:
    b = mcp_mongo.bridge
    print(f"DB={mcp_mongo.MONGODB_DB}  MODEL={mcp_mongo.GEMINI_MODEL}  URI={mcp_mongo.MONGODB_URI}")
    print("Starting MCP bridge (spawns mongodb-mcp-server via npx)…")
    t0 = time.time()
    ok = b.start(timeout=90)
    print(f"  ready={ok}  in {time.time()-t0:.1f}s  error={b.error}")
    if not ok:
        return 1
    print(f"  tools ({len(b.tool_names())}): {', '.join(b.tool_names())}")

    # 1) Direct MCP tool call (no Gemini) — proves MCP <-> mongod.
    print("\n[1] Direct MCP insert via tool call…")
    ins = b.call_tool("insert-many", {
        "database": mcp_mongo.MONGODB_DB,
        "collection": "mcp_smoke",
        "documents": [{"kind": "smoke", "n": 1, "ts": time.time()},
                      {"kind": "smoke", "n": 2, "ts": time.time()}],
    })
    print("   insert result:", json.dumps(ins, default=str)[:300])

    cnt = b.call_tool("count", {"database": mcp_mongo.MONGODB_DB, "collection": "mcp_smoke"})
    print("   count result:", json.dumps(cnt, default=str)[:300])

    # 2) Gemini-driven query — Gemini chooses the MCP tools.
    print("\n[2] Gemini function-calling over MCP…")
    res = b.gemini_query(
        "How many documents are in the 'mcp_smoke' collection, and what are the 'n' values? "
        "Use the MongoDB tools to find out, then tell me."
    )
    print("   ok:", res.get("ok"), "error:", res.get("error"))
    print("   trace:")
    for step in res.get("trace", []):
        print(f"     - {step['tool']}({json.dumps(step['args'], default=str)[:120]}) "
              f"ok={step['ok']} -> {str(step.get('result',''))[:120]}")
    print("   ANSWER:", res.get("answer"))

    b.stop()
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
