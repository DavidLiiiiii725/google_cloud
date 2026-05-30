"""FastAPI entry point for the Transport Agent.

Mirrors greenhouse/backend/main.py: SSE log stream, a trace endpoint the UI polls,
and action endpoints. The Transport Agent is event-driven (fired by the orchestrator
or a manual button), not a heartbeat loop — so no background scheduler here.
"""
from __future__ import annotations
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from . import db, events, workflow, pipeline, seed


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.get_db()
    # Auto-seed demo farms if the blackboard is empty, so the UI is never blank.
    if db.farms().count_documents({"scenario_id": "default"}) == 0:
        n = seed.seed_farms()
        events.emit("INFO", f"Seeded {n} demo farms")
    events.emit("INFO", "Transport Agent online — blackboard mode (MongoDB)")
    yield


app = FastAPI(title="Transport Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/api/transport/health")
def health():
    return {"ok": True, "db_mode": db.db_mode()}


@app.post("/api/transport/plan")
async def make_plan():
    """Build the initial consolidated plan from current farms."""
    plan = await pipeline.build_plan(trigger="initial")
    return {"plan_id": plan["_id"], "vehicles": len(plan["vehicles"]), "cost": plan["cost"]}


@app.post("/api/transport/seed")
def reseed():
    """Reset the blackboard to the clean 5-farm baseline (clears any prior storm)."""
    n = seed.seed_farms()
    workflow.tracker.reset()
    events.emit("INFO", f"Reset — {n} demo farms restored")
    return {"ok": True, "farms": n}


@app.post("/api/transport/replan")
async def replan():
    """Headline path — fire the storm (block roads, drop yields) then re-plan.
    Applying the disruption here makes one UI button drive the whole cascade."""
    seed.fire_storm()
    plan = await pipeline.replan()
    return {"plan_id": plan["_id"], "replan_of": plan["replan_of"],
            "diff": plan["reasoning"]["replan_diff"]}


@app.get("/api/transport/farms")
def list_farms():
    """All farms in the scenario, for map display (includes blocked ones)."""
    farms = db.farms().find({"scenario_id": "default"})
    return {"farms": [
        {"id": f["_id"], "name": f.get("name", f["_id"]),
         "lat": f["location"]["lat"], "lng": f["location"]["lng"],
         "access": f.get("access", "open"), "yield_status": f.get("yield_status", "normal")}
        for f in farms
    ]}


@app.get("/api/transport/plan/latest")
def latest_plan():
    p = db.transport_plans().find(
        {"scenario_id": "default", "status": "committed"}, sort=[("updated_at", -1)], limit=1)
    return {"plan": p[0] if p else None}


@app.get("/api/transport/trace")
def trace():
    return workflow.tracker.get()


@app.get("/api/transport/stream/logs")
async def stream_logs():
    async def gen():
        async for entry in events.subscribe():
            yield {"data": json.dumps(entry)}
    return EventSourceResponse(gen())
