"""FastAPI entry point. Wires all routes and a background scheduler."""
from __future__ import annotations
import asyncio
import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from . import db, state, events, pipeline, approve, images, seed, config, workflow
from .models import SensorInput


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.get_db()
    # Ensure baseline exists
    if db.historical_climate().count_documents({}) == 0:
        seed.seed()
    images.ensure_assets()
    events.emit("INFO", "Greenhouse online — monitoring 3 zones (seedling, growing, harvest)")
    # background heartbeat — slow degrade/recovery loop
    task = asyncio.create_task(_heartbeat())
    try:
        yield
    finally:
        task.cancel()


async def _heartbeat():
    """Periodically re-run rules to update zone health & resolve cleared incidents, even without slider input."""
    while True:
        await asyncio.sleep(2.0)
        try:
            await pipeline.run_cycle()
        except Exception as e:
            print(f"[heartbeat] {e}")


app = FastAPI(title="Agent Greenhouse", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])

# Serve placeholder plant images
app.mount("/assets", StaticFiles(directory=str(config.ASSETS_DIR)), name="assets")


@app.get("/")
def root_redirect():
    """Convenience: serve index.html if present at project root."""
    idx = config.ROOT / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {"ok": True, "service": "agent-greenhouse"}


@app.post("/api/sensor")
async def post_sensor(payload: SensorInput, bg: BackgroundTasks):
    """Sliders post here. Returns immediately; pipeline runs in background."""
    patch = payload.model_dump(exclude_none=True)
    stress = patch.pop("trigger_stress", False)
    state.update_sensors(patch)
    bg.add_task(pipeline.run_cycle, stress)
    return {"ok": True, "stress_test": stress}


@app.get("/api/status")
def get_status():
    sensors = state.get_sensors()
    zh = state.get_zone_health()
    actuator = state.get_actuator()
    inc_id = state.get_incident_id()
    countdown = approve.countdown_remaining(inc_id) if inc_id else None
    return {
        "sensors": sensors,
        "vpd": state.vpd_kpa(sensors["temperature"], sensors["humidity"]),
        "zone_health": zh,
        "actuator": actuator.model_dump(),
        "severity": state.get_severity(),
        "incident_id": inc_id,
        "countdown_remaining": countdown,
        "db_mode": db.db_mode(),
    }


@app.get("/api/incident/latest")
def get_latest_incident():
    inc_id = state.get_incident_id()
    if not inc_id:
        return {"incident": None}
    inc = db.incidents().find_one({"_id": inc_id})
    if not inc:
        return {"incident": None}
    inc = dict(inc)
    inc["countdown_remaining"] = approve.countdown_remaining(inc_id)
    return {"incident": inc}


@app.post("/api/incident/{incident_id}/approve")
async def approve_incident(incident_id: str):
    inc = db.incidents().find_one({"_id": incident_id})
    if not inc:
        raise HTTPException(404, "incident not found")
    events.emit("ACT", "Operator approved", incident_id=incident_id)
    approve.cancel_countdown(incident_id)
    await pipeline.execute_plan(incident_id, "operator")
    return {"ok": True}


@app.post("/api/incident/{incident_id}/dismiss")
def dismiss_incident(incident_id: str):
    inc = db.incidents().find_one({"_id": incident_id})
    if not inc:
        raise HTTPException(404, "incident not found")
    approve.cancel_countdown(incident_id)
    db.incidents().update_one({"_id": incident_id}, {"$set": {"status": "dismissed"}})
    if state.get_incident_id() == incident_id:
        state.set_incident_id(None)
    workflow.tracker.set("execute", "dismissed", message="Operator dismissed — plan discarded")
    events.emit("HITL", "Operator dismissed incident", incident_id=incident_id)
    return {"ok": True}


@app.get("/api/history")
def get_history():
    docs = db.live_telemetry().find(sort=[("ts", -1)], limit=60)
    docs = list(reversed(docs))
    return {"history": [
        {"ts": d.get("ts"), "temperature": d.get("temperature"),
         "humidity": d.get("humidity"), "vpd": d.get("vpd"),
         "co2": d.get("co2"), "par": d.get("par"),
         "zone_health": d.get("zone_health", {})}
        for d in docs
    ]}


@app.post("/api/reset")
def reset():
    inc_id = state.get_incident_id()
    if inc_id:
        approve.cancel_countdown(inc_id)
    state.reset()
    workflow.tracker.reset()
    pipeline._last_execution_at = 0.0  # clear post-execution cooldown so a fresh stress can fire immediately
    # Close all open incidents
    db.incidents().update_one({"status": "open"}, {"$set": {"status": "reset"}})
    events.emit("INFO", "Reset — baseline restored")
    return {"ok": True}


@app.get("/api/stream/logs")
async def stream_logs():
    async def gen():
        async for entry in events.subscribe():
            yield {"data": json.dumps(entry)}
    return EventSourceResponse(gen())


@app.get("/api/agent/trace")
def get_agent_trace():
    """Per-stage reasoning trace for the current pipeline run.
    Frontend polls this to visualise the agentic workflow live."""
    return workflow.tracker.get()
