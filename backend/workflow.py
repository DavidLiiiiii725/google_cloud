"""Workflow tracker — exposes the agent's per-stage reasoning to the frontend.

Six stages run in order whenever an anomaly fires the pipeline:
  1. rules      — z-score anomaly detection
  2. confidence — 5-component weighted score (pre-vision and post-vision)
  3. vision     — Gemini 2.0 Flash multimodal call on zone images
  4. action     — Gemini 2.0 Flash tool-use plan generation
  5. decide     — auto-approve engine (override rules + confidence matrix)
  6. execute    — actuator dispatch (after countdown or operator approval)
"""
from __future__ import annotations
import threading
import time

STAGES = [
    {"id": "rules",      "name": "RULES ENGINE",   "tool": "z-score anomaly detection vs monthly baseline"},
    {"id": "confidence", "name": "CONFIDENCE",     "tool": "5-component weighted score"},
    {"id": "vision",     "name": "VISION",         "tool": "Gemini 2.0 Flash · multimodal"},
    {"id": "action",     "name": "ACTION PLAN",    "tool": "Gemini 2.0 Flash · tool use"},
    {"id": "decide",     "name": "DECISION",       "tool": "auto-approve engine"},
    {"id": "execute",    "name": "EXECUTION",      "tool": "actuator dispatch"},
]


class WorkflowTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._stages: dict[str, dict] = {}
        self._incident_id: str | None = None
        self._severity: str | None = None
        self._started_at: float | None = None

    def start(self):
        """Reset for a new pipeline cycle. Called when an anomaly is first detected."""
        with self._lock:
            self._stages = {}
            self._incident_id = None
            self._severity = None
            self._started_at = time.time()

    def set(self, stage_id: str, status: str, message: str = "", data: dict | None = None):
        """status ∈ {running, done, waiting, dismissed, failed}"""
        with self._lock:
            stage_def = next((s for s in STAGES if s["id"] == stage_id), None)
            if not stage_def:
                return
            existing = self._stages.get(stage_id, {})
            now = time.time()
            entry = {
                "id": stage_id,
                "name": stage_def["name"],
                "tool": stage_def["tool"],
                "status": status,
                "message": message or existing.get("message", ""),
                "data": data if data is not None else existing.get("data", {}),
                "started_at": existing.get("started_at") or now,
                "updated_at": now,
            }
            if status in ("done", "dismissed", "failed"):
                entry["duration"] = entry["updated_at"] - entry["started_at"]
            self._stages[stage_id] = entry

    def attach_incident(self, incident_id: str, severity: str):
        with self._lock:
            self._incident_id = incident_id
            self._severity = severity

    def get(self) -> dict:
        """Snapshot of the current trace, with idle placeholders for stages not yet run."""
        with self._lock:
            stages_out = []
            for sdef in STAGES:
                if sdef["id"] in self._stages:
                    stages_out.append(self._stages[sdef["id"]])
                else:
                    stages_out.append({
                        "id": sdef["id"],
                        "name": sdef["name"],
                        "tool": sdef["tool"],
                        "status": "idle",
                        "message": "",
                        "data": {},
                    })
            return {
                "active": bool(self._stages),
                "incident_id": self._incident_id,
                "severity": self._severity,
                "started_at": self._started_at,
                "stages": stages_out,
            }

    def reset(self):
        with self._lock:
            self._stages = {}
            self._incident_id = None
            self._severity = None
            self._started_at = None


tracker = WorkflowTracker()
