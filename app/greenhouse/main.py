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

from . import db, state, events, pipeline, approve, images, seed, config, workflow, farm_publish, coordination
from .models import SensorInput
from app import mcp_mongo


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.get_db()
    # Ensure baseline exists
    if db.historical_climate().count_documents({}) == 0:
        seed.seed()
    images.ensure_assets()
    events.emit("INFO", "Greenhouse online — monitoring 3 zones (seedling, growing, harvest)")
    # Publish this greenhouse's farm doc immediately so the Transport Agent sees it
    # on startup (without waiting for the first stress event to fire).
    try:
        farm_publish.publish_farm(force=True)
    except Exception as e:
        events.emit("INFO", f"initial farm_publish failed: {e}")
    # Spin up the MongoDB MCP bridge (Gemini -> MCP -> real mongod). Done off the
    # event loop so a slow npx/mongod start never blocks app startup; if it fails
    # the rest of the app keeps working and /api/mcp/status reports the error.
    async def _start_mcp():
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, mcp_mongo.bridge.start)
        if ok:
            events.emit("INFO", f"MongoDB MCP connected — {len(mcp_mongo.bridge.tool_names())} tools available")
        else:
            events.emit("INFO", f"MongoDB MCP unavailable: {mcp_mongo.bridge.error}")
    asyncio.create_task(_start_mcp())
    # background heartbeat — slow degrade/recovery loop
    task = asyncio.create_task(_heartbeat())
    try:
        yield
    finally:
        task.cancel()
        mcp_mongo.bridge.stop()


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
    """Serve the unified app at web/index.html."""
    idx = config.WEB_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {"ok": True, "service": "agent-greenhouse"}


@app.get("/api/farm/state")
def farm_state():
    """Expose this greenhouse's farm doc so the UI can show what's been published."""
    doc = db.farms().find_one({"_id": config.FARM_ID})
    return {"farm": doc, "farm_id": config.FARM_ID, "scenario": config.SCENARIO}


@app.post("/api/farm/publish")
def force_publish():
    """Manual republish — useful after editing zone health to confirm the blackboard sees it."""
    doc = farm_publish.publish_farm(force=True)
    return {"ok": True, "farm": doc}


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


@app.get("/api/policy")
def get_policy():
    """Current auto-approval policy — which severities auto-execute (no human
    approval), confidence thresholds, countdown timeouts, and quiet hours."""
    return {"policy": approve.get_policy(), "default": approve.DEFAULT_POLICY}


@app.post("/api/policy")
def set_policy(patch: dict):
    """Update the auto-approval policy at runtime. Examples:
      {"severity": {"critical": {"mode": "auto_immediate"}}}   # critical runs with no approval
      {"severity": {"catastrophic": {"mode": "auto_countdown", "timeout_sec": 20}}}
      {"quiet_hours": {"enabled": false}}                       # allow auto-execute 24/7
    """
    updated = approve.update_policy(patch or {})
    events.emit("INFO", "Approval policy updated")
    return {"ok": True, "policy": updated}


@app.post("/api/policy/reset")
def reset_policy():
    return {"ok": True, "policy": approve.reset_policy()}


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


# ---- MongoDB MCP (Phase 4): Gemini calls the MongoDB MCP server -------------- #
@app.get("/api/mcp/status")
def mcp_status():
    """Is the MongoDB MCP bridge connected, and what tools did it expose?"""
    b = mcp_mongo.bridge
    return {
        "ready": b.is_ready(),
        "error": b.error,
        "db": mcp_mongo.MONGODB_DB,
        "model": mcp_mongo.GEMINI_MODEL,
        "tools": b.tool_summaries() if b.is_ready() else [],
    }


# ---- Coordination overview: the whole Greenhouse → Transport → Merchant chain ---- #
@app.get("/api/coordination/state")
def coordination_state():
    """One blackboard snapshot across all three agents for the COORDINATION tab.
    Pure DB reads (no LLM) so the UI can poll it cheaply."""
    return coordination.snapshot()


@app.post("/api/coordination/narrate")
async def coordination_narrate():
    """Have Gemini read the shared collections via the MongoDB MCP tools and write a
    natural-language situation report on the three-agent cascade. This is the Phase-4
    centerpiece: one LLM, real MongoDB MCP tool calls, spanning all three agents."""
    if not mcp_mongo.bridge.is_ready():
        await asyncio.get_event_loop().run_in_executor(None, mcp_mongo.bridge.start)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: mcp_mongo.bridge.gemini_query(coordination.narration_request()))
    events.emit("REASON", f"Coordination narration → {len(result.get('trace', []))} MongoDB MCP call(s)")
    return result


@app.post("/api/mcp/query")
async def mcp_query(payload: dict):
    """Run a real Gemini function-calling loop over the MongoDB MCP tools.

    Body: {"request": "<natural language>", "allow_tools": ["find", ...]?}
    Gemini decides which MongoDB MCP tools to call; each runs against the real
    mongod. Returns the final answer plus the tool-call trace.
    """
    request = (payload or {}).get("request", "").strip()
    if not request:
        raise HTTPException(400, "missing 'request'")
    allow = (payload or {}).get("allow_tools")
    if not mcp_mongo.bridge.is_ready():
        # Last-chance lazy start so the endpoint is usable even if startup raced.
        await asyncio.get_event_loop().run_in_executor(None, mcp_mongo.bridge.start)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: mcp_mongo.bridge.gemini_query(request, allow_tools=allow))
    events.emit("REASON", f"MCP query → {len(result.get('trace', []))} MongoDB tool call(s)")
    return result
