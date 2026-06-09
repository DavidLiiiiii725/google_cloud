"""FastAPI entry point for the Merchant Agent (port 8002).

Mirrors app/transport/main.py: SSE log stream, a trace endpoint the UI polls, and action
endpoints. Unlike the Transport Agent, the Merchant runs a small background WATCHER that
polls the blackboard for a new committed transport_plan or a storm-state change and
re-allocates automatically — that's what makes the Greenhouse → Transport → Merchant
cascade flow end-to-end without any agent calling another.
"""
from __future__ import annotations
import asyncio
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from . import db, events, workflow, pipeline, seed, agent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.get_db()
    n = seed.seed_buyers()
    events.emit("INFO", f"Merchant Agent online — {n} buyers on the demand book (blackboard mode)")
    task = asyncio.create_task(_watcher())
    try:
        yield
    finally:
        task.cancel()


async def _watcher():
    """Poll the blackboard; re-allocate when Transport commits a new plan or a storm flips."""
    await asyncio.sleep(2.0)  # let the other agents come up first
    while True:
        try:
            await pipeline.react_to_blackboard()
        except Exception as e:
            print(f"[watcher] {e}")
        await asyncio.sleep(1.2)


app = FastAPI(title="Merchant Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/api/merchant/health")
def health():
    return {"ok": True, "db_mode": db.db_mode()}


@app.get("/api/merchant/world")
def world_state():
    storm = db.world_events().find_one(
        {"scenario_id": "default", "type": "storm", "status": "active"})
    return {"storm": storm}


@app.get("/api/merchant/buyers")
def list_buyers():
    bs = db.buyers().find({"scenario_id": "default"})
    return {"buyers": sorted(bs, key=lambda b: b.get("priority", 99))}


@app.get("/api/merchant/supply")
def current_supply():
    """Live arriving-supply snapshot (no commit) — what the Merchant would allocate now."""
    plan = pipeline.latest_committed_plan()
    farms = db.farms().find({"scenario_id": "default"})
    info = agent.arriving_supply(plan, farms)
    return {"plan_id": plan["_id"] if plan else None, **info}


@app.post("/api/merchant/allocate")
async def make_allocation():
    """Manually run a full allocation against the current blackboard state."""
    order = await pipeline.build_allocation(trigger="manual")
    return {"order_id": order["_id"], "fill_rate": order["fulfillment"]["fill_rate"],
            "revenue_cents": order["revenue_cents"]}


@app.get("/api/merchant/orders/latest")
def latest_order():
    return {"order": pipeline.latest_order()}


@app.get("/api/merchant/trace")
def trace():
    return workflow.tracker.get()


@app.post("/api/merchant/seed")
def reseed():
    """Reset the demand book and clear prior orders for a clean demo run."""
    n = seed.seed_buyers()
    seed.reset_orders()
    workflow.tracker.reset()
    # Force the watcher to re-evaluate from scratch on its next tick.
    pipeline._last_plan_id = None
    pipeline._last_storm_id = None
    pipeline._ever_allocated = False
    events.emit("INFO", f"Merchant reset — {n} buyers restored, orders cleared")
    return {"ok": True, "buyers": n}


@app.get("/api/merchant/stream/logs")
async def stream_logs():
    async def gen():
        async for entry in events.subscribe():
            yield {"data": json.dumps(entry)}
    return EventSourceResponse(gen())
