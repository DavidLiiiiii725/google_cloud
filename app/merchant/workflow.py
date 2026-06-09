"""Workflow tracker for the Merchant Agent.

Structurally identical to app/transport/workflow.py — same WorkflowTracker the UI polls
at /api/merchant/trace — but with the Merchant's OWN five stages. This convention
compatibility lets the shared UI render all three agents' reasoning in the same style.

The five stages separate REASONING (read the arriving supply, assess demand, decide the
allocation policy, set scarcity prices) from the COMMIT. The reasoning is what makes this
an agent: it does not just divide a number — it prioritizes essential buyers under
scarcity and explains why, then prices the shortfall.
"""
from __future__ import annotations
import threading
import time

STAGES = [
    {"id": "read",     "name": "READ SUPPLY",    "tool": "MongoDB MCP · transport_plans + farms"},
    {"id": "demand",   "name": "DEMAND BOOK",    "tool": "buyers collection · open orders"},
    {"id": "allocate", "name": "ALLOCATE",       "tool": "priority fair-share reasoning"},
    {"id": "price",    "name": "SCARCITY PRICE", "tool": "shortfall → unit pricing"},
    {"id": "commit",   "name": "COMMIT ORDERS",  "tool": "write market_orders to blackboard"},
]


class WorkflowTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._stages: dict[str, dict] = {}
        self._order_id: str | None = None
        self._trigger: str | None = None
        self._started_at: float | None = None

    def start(self, trigger: str = "initial"):
        with self._lock:
            self._stages = {}
            self._order_id = None
            self._trigger = trigger
            self._started_at = time.time()

    def set(self, stage_id: str, status: str, message: str = "", data: dict | None = None):
        """status ∈ {running, done, waiting, failed}"""
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
            if status in ("done", "failed"):
                entry["duration"] = entry["updated_at"] - entry["started_at"]
            self._stages[stage_id] = entry

    def attach_order(self, order_id: str, trigger: str):
        with self._lock:
            self._order_id = order_id
            self._trigger = trigger

    def get(self) -> dict:
        with self._lock:
            stages_out = []
            for sdef in STAGES:
                if sdef["id"] in self._stages:
                    stages_out.append(self._stages[sdef["id"]])
                else:
                    stages_out.append({
                        "id": sdef["id"], "name": sdef["name"], "tool": sdef["tool"],
                        "status": "idle", "message": "", "data": {},
                    })
            return {
                "active": bool(self._stages),
                "order_id": self._order_id,
                "trigger": self._trigger,
                "started_at": self._started_at,
                "stages": stages_out,
            }

    def reset(self):
        with self._lock:
            self._stages = {}
            self._order_id = None
            self._trigger = None
            self._started_at = None


tracker = WorkflowTracker()
