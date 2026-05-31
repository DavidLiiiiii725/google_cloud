"""Auto-approve engine: override rules + confidence matrix + countdown timer."""
from __future__ import annotations
import asyncio
import datetime as dt
import time
from . import db, state, events

# Decision outcomes
AUTO_IMMEDIATE = "auto_immediate"
AUTO_COUNTDOWN = "auto_countdown"
HITL = "hitl"


def _is_night() -> bool:
    h = dt.datetime.now().hour
    return h >= 22 or h < 6


def _recent_auto_same_zone(zone: str, within_sec: int = 1800) -> bool:
    cutoff = time.time() - within_sec
    recent = db.incidents().find(sort=[("ts", -1)], limit=20)
    for inc in recent:
        if inc.get("ts", 0) < cutoff:
            break
        if inc.get("status") == "auto_approved" and zone in inc.get("affected_zones", []):
            return True
    return False


def decide(incident: dict, sensors: dict, vision: dict) -> dict:
    """Return {decision, timeout_sec, reason}."""
    severity = incident["severity"]
    confidence = incident["confidence"]
    plan = incident["plan"]
    risk = plan.get("risk", "low")
    steps = plan.get("steps", [])
    tools = {s["tool"] for s in steps}
    affected = incident.get("affected_zones", [])

    # --- Hard override rules ---
    if "activate_irrigation" in tools and sensors["tank_level"] < 15:
        return {"decision": HITL, "timeout_sec": 600, "reason": "Tank level <15% with irrigation in plan — pump damage risk"}

    if _is_night() and risk in ("medium", "high"):
        return {"decision": HITL, "timeout_sec": 1800, "reason": "Night hours — medium/high risk plan queued for morning"}

    sensor_age = time.time() - sensors.get("ts", time.time())
    if sensor_age > 120:
        return {"decision": HITL, "timeout_sec": 600, "reason": "Sensor data stale (>2 cycles)"}

    if vision.get("severity_confirmed") == "nominal" and severity in ("critical", "catastrophic"):
        return {"decision": HITL, "timeout_sec": 600, "reason": "Vision shows healthy plants but sensors critical — sensor malfunction suspected"}

    for z in affected:
        if _recent_auto_same_zone(z):
            return {"decision": HITL, "timeout_sec": 600, "reason": f"Zone {z} re-triggered within 30 min of previous auto-action"}

    # --- Confidence matrix ---
    if severity == "warning" and confidence >= 0.75:
        if risk == "low":
            return {"decision": AUTO_IMMEDIATE, "timeout_sec": 0, "reason": "Warning + high confidence + low-risk plan"}
        return {"decision": AUTO_COUNTDOWN, "timeout_sec": 60, "reason": "Warning + medium-risk plan — 60s countdown"}

    if severity == "critical":
        if confidence >= 0.90:
            return {"decision": AUTO_COUNTDOWN, "timeout_sec": 30, "reason": "Critical + ≥0.90 confidence — 30s countdown"}
        return {"decision": HITL, "timeout_sec": 600, "reason": "Critical but confidence <0.90 — human review"}

    if severity == "catastrophic":
        return {"decision": HITL, "timeout_sec": 300, "reason": "Catastrophic severity — human review required"}

    # Default fall-through
    return {"decision": HITL, "timeout_sec": 600, "reason": "Default — operator review"}


# ---- Countdown task management --------------------------------------------

_countdown_tasks: dict[str, asyncio.Task] = {}
_countdown_state: dict[str, dict] = {}


async def start_countdown(incident_id: str, timeout_sec: int, on_fire):
    """Schedule auto-approval after `timeout_sec`. Stores progress for the status endpoint."""
    _countdown_state[incident_id] = {"started": time.time(), "timeout": timeout_sec, "active": True}
    try:
        await asyncio.sleep(timeout_sec)
        if _countdown_state.get(incident_id, {}).get("active"):
            events.emit("AUTO", f"Auto-approved after {timeout_sec}s — operator did not intervene", incident_id=incident_id)
            await on_fire(incident_id, "auto_timeout")
    except asyncio.CancelledError:
        pass
    finally:
        _countdown_state.get(incident_id, {})["active"] = False


def cancel_countdown(incident_id: str):
    t = _countdown_tasks.pop(incident_id, None)
    if t and not t.done():
        t.cancel()
    if incident_id in _countdown_state:
        _countdown_state[incident_id]["active"] = False


def register_task(incident_id: str, task: asyncio.Task):
    _countdown_tasks[incident_id] = task


def countdown_remaining(incident_id: str) -> float | None:
    s = _countdown_state.get(incident_id)
    if not s or not s.get("active"):
        return None
    elapsed = time.time() - s["started"]
    return max(0.0, s["timeout"] - elapsed)
