"""Demo orchestrator — fires the storm cascade.

This is the ~50 lines Path A needs. It is the ONLY thing that writes world_events.
Run it (or hit its endpoints) during the demo to make the cascade visible:

    python -m orchestrator.storm

Sequence:
  1. write an active storm to world_events
  2. (greenhouse agents react on their own heartbeat — lower yields, write farms)
  3. wait until farms reflect reduced yield / blocked access
  4. POST /api/transport/replan  → Transport writes a new tagged plan
  5. (Merchant reacts to the new plan on its own)

For a hackathon demo you can also just call steps 1 and 4 manually from the UI.
Keeping it scriptable means a clean, repeatable run on stage.
"""
from __future__ import annotations
import time
import json
import urllib.request

# These would come from config in the real merge.
import os
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB = os.getenv("MONGODB_DB", "agent_greenhouse")
TRANSPORT_URL = os.getenv("TRANSPORT_URL", "http://localhost:8001")
SCENARIO = "default"


def _db():
    if MONGODB_URI:
        from pymongo import MongoClient
        return MongoClient(MONGODB_URI)[MONGODB_DB]
    raise SystemExit("Set MONGODB_URI (orchestrator needs the shared cluster).")


def fire_storm(severity: str = "critical", yield_multiplier: float = 0.6,
               roads_blocked: list[str] | None = None) -> str:
    db = _db()
    evt_id = f"evt-storm-{int(time.time())}"
    db["world_events"].insert_one({
        "_id": evt_id, "scenario_id": SCENARIO, "type": "storm", "status": "active",
        "severity": severity,
        "effects": {"yield_multiplier": yield_multiplier, "roads_blocked": roads_blocked or []},
        "created_at": time.time(), "updated_at": time.time(), "updated_by": "orchestrator",
    })
    print(f"[orchestrator] storm {evt_id} active (severity={severity})")
    return evt_id


def trigger_replan() -> dict:
    req = urllib.request.Request(
        TRANSPORT_URL.rstrip("/") + "/api/transport/replan",
        data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


if __name__ == "__main__":
    evt = fire_storm()
    print("[orchestrator] waiting 8s for farmer agents to revise yields…")
    time.sleep(8)
    result = trigger_replan()
    print("[orchestrator] re-plan complete:")
    print(json.dumps(result, indent=2))
