"""Cross-agent coordination overview.

The Greenhouse service already hosts the Gemini ↔ MongoDB MCP bridge (app/mcp_mongo.py),
so it is the natural place to expose a *whole supply chain* view. This module reads the
shared blackboard collections all three agents read/write — farms, world_events,
transport_plans, market_orders — and folds them into one snapshot the unified UI's
COORDINATION tab renders as a live Greenhouse → Transport → Merchant flow.

Nothing here drives the agents; it only observes the documents they exchange. The
narration endpoint hands the real reasoning to Gemini over the MongoDB MCP tools.
"""
from __future__ import annotations
import time
from . import db, config

SCENARIO = config.SCENARIO


def _latest(coll, status="committed"):
    docs = coll.find({"scenario_id": SCENARIO, "status": status},
                     sort=[("updated_at", -1)], limit=1)
    return docs[0] if docs else None


def _agent_status(last_ts: float | None, now: float, *, active_window=8.0) -> str:
    """A coarse liveness/working signal from the most recent write timestamp."""
    if last_ts is None:
        return "idle"
    return "working" if (now - last_ts) < active_window else "online"


def snapshot() -> dict:
    """One blackboard snapshot for the coordination view. Cheap — pure DB reads, no LLM."""
    now = time.time()
    farms = db.farms().find({"scenario_id": SCENARIO})
    storm = db.world_events().find_one(
        {"scenario_id": SCENARIO, "type": "storm", "status": "active"})
    plan = _latest(db.transport_plans())
    order = _latest(db.market_orders())

    blocked = [f for f in farms if f.get("access") == "blocked"]
    reduced = [f for f in farms if f.get("yield_status") == "reduced"]

    # ---- Greenhouse node ----
    gh_farm = next((f for f in farms if f["_id"] == config.FARM_ID), None)
    gh_updated = gh_farm.get("updated_at") if gh_farm else None
    greenhouse = {
        "agent": "greenhouse",
        "status": _agent_status(gh_updated, now),
        "farm_id": config.FARM_ID,
        "yield_status": gh_farm.get("yield_status") if gh_farm else "unknown",
        "stock_kg": round(sum(s.get("quantity_kg", 0) for s in (gh_farm or {}).get("stock", [])), 1),
        "farms_total": len(farms),
        "farms_blocked": len(blocked),
        "farms_reduced": len(reduced),
        "updated_at": gh_updated,
    }

    # ---- Transport node ----
    transport = {
        "agent": "transport",
        "status": _agent_status(plan.get("updated_at") if plan else None, now),
        "plan_id": plan["_id"] if plan else None,
        "trigger": plan.get("trigger") if plan else None,
        "vehicles": len(plan.get("vehicles", [])) if plan else 0,
        "stops": sum(len(v.get("stops", [])) for v in plan.get("vehicles", [])) if plan else 0,
        "unrouted": len(plan.get("unrouted", [])) if plan else 0,
        "cost_cents": (plan.get("cost", {}) or {}).get("total_cents") if plan else None,
        "replan_diff": (plan.get("reasoning", {}) or {}).get("replan_diff") if plan else None,
        "updated_at": plan.get("updated_at") if plan else None,
    }

    # ---- Merchant node ----
    f = (order or {}).get("fulfillment", {})
    merchant = {
        "agent": "merchant",
        "status": _agent_status(order.get("updated_at") if order else None, now),
        "order_id": order["_id"] if order else None,
        "from_plan": order.get("from_plan") if order else None,
        "fill_rate": f.get("fill_rate"),
        "allocated_kg": f.get("allocated_kg"),
        "requested_kg": f.get("requested_kg"),
        "revenue_cents": order.get("revenue_cents") if order else None,
        "scarce_crops": (order.get("pricing", {}) or {}).get("scarce_crops", []) if order else [],
        "rationale": (order.get("reasoning", {}) or {}).get("rationale") if order else None,
        "gemini_source": (order.get("reasoning", {}) or {}).get("gemini_source") if order else None,
        "replan_diff": (order.get("reasoning", {}) or {}).get("replan_diff") if order else None,
        "updated_at": order.get("updated_at") if order else None,
    }

    # ---- Cascade coherence: does the Merchant's order reflect the latest plan? ----
    in_sync = bool(order and plan and order.get("from_plan") == plan["_id"])

    return {
        "ts": now,
        "scenario": SCENARIO,
        "db_mode": db.db_mode(),
        "storm": bool(storm),
        "storm_event": storm,
        "nodes": {"greenhouse": greenhouse, "transport": transport, "merchant": merchant},
        "links": [
            {"from": "greenhouse", "to": "transport", "label": "farms",
             "active": greenhouse["status"] == "working"},
            {"from": "transport", "to": "merchant", "label": "transport_plans",
             "active": transport["status"] == "working"},
        ],
        "in_sync": in_sync,
        "headline": _headline(storm, transport, merchant),
    }


def _headline(storm, transport, merchant) -> str:
    if storm:
        bits = ["⚡ Storm active"]
        if transport.get("replan_diff"):
            bits.append(transport["replan_diff"].get("summary", "transport re-planned"))
        if merchant.get("fill_rate") is not None:
            bits.append(f"market filling {round(merchant['fill_rate']*100)}%")
        return " — ".join(bits)
    if merchant.get("fill_rate") is not None:
        return f"Nominal — supply chain at {round(merchant['fill_rate']*100)}% fulfillment"
    return "Nominal — awaiting first plan"


# --------------------------------------------------------------------------- #
# Gemini ↔ MongoDB MCP narration of the whole chain.
# --------------------------------------------------------------------------- #
NARRATION_REQUEST = (
    "You are the supply-chain coordinator for a three-agent farm system that shares one "
    "MongoDB database. Using the MongoDB tools, read the current state across these "
    "collections in the '{db}' database and write a 3-4 sentence situation report a human "
    "operator could read aloud:\n"
    "  • world_events  — is a storm active right now?\n"
    "  • farms         — how many farms, how many blocked or with reduced/destroyed yield?\n"
    "  • transport_plans (status='committed', newest) — vehicles, stops, unrouted, cost\n"
    "  • market_orders   (status='committed', newest) — fill rate, revenue, any scarce crops\n"
    "Explain how the Greenhouse → Transport → Merchant cascade is currently coordinated "
    "(e.g. a storm cut yields, transport re-routed, the merchant reallocated and re-priced). "
    "State concrete numbers you actually read. End with one line: 'CHAIN: HEALTHY' or "
    "'CHAIN: DISRUPTED'."
)


def narration_request() -> str:
    from app import mcp_mongo
    return NARRATION_REQUEST.format(db=mcp_mongo.MONGODB_DB)
