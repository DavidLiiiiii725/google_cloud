"""Auto-approve engine: safety overrides + configurable per-severity policy + countdown timer.

The policy is runtime-configurable (GET/POST /api/policy) so an operator can decide which
severities execute WITHOUT human approval and during which hours — without code changes.
Hard safety overrides (low tank, stale sensors, sensor/vision mismatch, rapid re-trigger)
always win regardless of policy, unless explicitly disabled."""
from __future__ import annotations
import asyncio
import copy
import datetime as dt
import threading
import time
from . import db, state, events

# Decision outcomes
AUTO_IMMEDIATE = "auto_immediate"
AUTO_COUNTDOWN = "auto_countdown"
HITL = "hitl"


# --- Runtime-configurable approval policy ----------------------------------- #
# mode per severity: "auto_immediate" (no approval, run now),
#                    "auto_countdown" (run after timeout unless operator intervenes),
#                    "hitl"           (wait for a human).
# min_confidence: below this the plan is downgraded to HITL no matter the mode.
DEFAULT_POLICY = {
    "severity": {
        "warning":      {"mode": "auto_countdown", "timeout_sec": 60,  "min_confidence": 0.75},
        "critical":     {"mode": "auto_countdown", "timeout_sec": 30,  "min_confidence": 0.90},
        "catastrophic": {"mode": "hitl",           "timeout_sec": 300, "min_confidence": 2.0},
    },
    # A low-risk plan whose severity mode is auto_countdown may skip the countdown.
    "allow_immediate_low_risk": True,
    # During quiet hours, force medium/high-risk plans to HITL (set enabled=false to
    # let plans auto-execute around the clock).
    "quiet_hours": {"enabled": True, "start": 22, "end": 6},
    # Master switch for the hard safety overrides below.
    "safety_overrides": True,
}

_policy = copy.deepcopy(DEFAULT_POLICY)
_policy_lock = threading.Lock()


def get_policy() -> dict:
    with _policy_lock:
        return copy.deepcopy(_policy)


def update_policy(patch: dict) -> dict:
    """Shallow-merge a patch into the policy. `severity` is merged per-tier so you can
    update just one severity (e.g. {"severity": {"critical": {"mode": "auto_immediate"}}})."""
    with _policy_lock:
        for key, val in (patch or {}).items():
            if key == "severity" and isinstance(val, dict):
                for tier, cfg in val.items():
                    if isinstance(cfg, dict):
                        _policy["severity"].setdefault(tier, {}).update(cfg)
            elif key == "quiet_hours" and isinstance(val, dict):
                _policy["quiet_hours"].update(val)
            else:
                _policy[key] = val
        return copy.deepcopy(_policy)


def reset_policy() -> dict:
    global _policy
    with _policy_lock:
        _policy = copy.deepcopy(DEFAULT_POLICY)
        return copy.deepcopy(_policy)


def _in_quiet_hours(qh: dict) -> bool:
    if not qh.get("enabled"):
        return False
    h = dt.datetime.now().hour
    start, end = qh.get("start", 22), qh.get("end", 6)
    if start <= end:
        return start <= h < end
    return h >= start or h < end  # wraps past midnight


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
    """Return {decision, timeout_sec, reason}. Safety overrides win first, then the
    runtime-configurable per-severity policy decides auto-execute vs human approval."""
    policy = get_policy()
    severity = incident["severity"]
    confidence = incident["confidence"]
    plan = incident["plan"]
    risk = plan.get("risk", "low")
    steps = plan.get("steps", [])
    tools = {s["tool"] for s in steps}
    affected = incident.get("affected_zones", [])

    # --- Hard safety overrides (always win unless disabled) ---
    if policy.get("safety_overrides", True):
        if "activate_irrigation" in tools and sensors["tank_level"] < 15:
            return {"decision": HITL, "timeout_sec": 600, "reason": "Tank level <15% with irrigation in plan — pump damage risk"}

        sensor_age = time.time() - sensors.get("ts", time.time())
        if sensor_age > 120:
            return {"decision": HITL, "timeout_sec": 600, "reason": "Sensor data stale (>2 cycles)"}

        if vision.get("severity_confirmed") == "nominal" and severity in ("critical", "catastrophic"):
            return {"decision": HITL, "timeout_sec": 600, "reason": "Vision shows healthy plants but sensors critical — sensor malfunction suspected"}

        for z in affected:
            if _recent_auto_same_zone(z):
                return {"decision": HITL, "timeout_sec": 600, "reason": f"Zone {z} re-triggered within 30 min of previous auto-action"}

    # --- Time gating: quiet hours force risky plans to a human ---
    if _in_quiet_hours(policy.get("quiet_hours", {})) and risk in ("medium", "high"):
        return {"decision": HITL, "timeout_sec": 1800, "reason": "Quiet hours — medium/high risk plan queued for operator"}

    # --- Per-severity policy ---
    cfg = policy.get("severity", {}).get(severity)
    if not cfg:
        return {"decision": HITL, "timeout_sec": 600, "reason": f"No policy for severity '{severity}' — operator review"}

    min_conf = cfg.get("min_confidence", 0.0)
    if confidence < min_conf:
        return {"decision": HITL, "timeout_sec": cfg.get("timeout_sec", 600),
                "reason": f"{severity.title()} but confidence {confidence:.2f} < {min_conf:.2f} threshold — human review"}

    mode = cfg.get("mode", "hitl")
    timeout = int(cfg.get("timeout_sec", 60))

    if mode == "hitl":
        return {"decision": HITL, "timeout_sec": timeout, "reason": f"Policy: '{severity}' requires human approval"}

    if mode == "auto_immediate":
        return {"decision": AUTO_IMMEDIATE, "timeout_sec": 0,
                "reason": f"Policy: '{severity}' auto-executes immediately (no approval)"}

    # auto_countdown — but a low-risk plan may skip the wait if allowed.
    if risk == "low" and policy.get("allow_immediate_low_risk", True):
        return {"decision": AUTO_IMMEDIATE, "timeout_sec": 0,
                "reason": f"Policy: '{severity}' + low-risk plan auto-executes immediately"}
    return {"decision": AUTO_COUNTDOWN, "timeout_sec": timeout,
            "reason": f"Policy: '{severity}' auto-executes in {timeout}s unless overridden"}


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
