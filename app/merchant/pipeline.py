"""Merchant Agent pipeline: read arriving supply → demand book → allocate →
scarcity-price → commit market_orders.

Mirrors app/transport/pipeline.py's shape: each stage reports to workflow.tracker and
emits a log line, with small pauses so a human audience can watch the reasoning light up.
The headline path is reallocate(), re-run after a storm re-plan — it writes a NEW order
tagged replaces the prior one, with a populated reasoning.replan_diff (the before/after).

The Merchant never calls the Transport Agent. It reads the committed transport_plan off
the blackboard; react_to_blackboard() (driven by the background watcher in main.py) is
what makes the three-agent cascade automatic: a new plan or a storm flips a flag here and
the allocation re-runs on its own.
"""
from __future__ import annotations
import asyncio
import time
import uuid
from . import db, events, workflow, agent, gemini_client, seed

STAGE_PAUSE = 0.35
SCENARIO = "default"


def _active_storm() -> dict | None:
    return db.world_events().find_one({"scenario_id": SCENARIO, "type": "storm", "status": "active"})


def latest_committed_plan() -> dict | None:
    plans = db.transport_plans().find(
        {"scenario_id": SCENARIO, "status": "committed"}, sort=[("updated_at", -1)], limit=1)
    return plans[0] if plans else None


def latest_order() -> dict | None:
    orders = db.market_orders().find(
        {"scenario_id": SCENARIO, "status": "committed"}, sort=[("updated_at", -1)], limit=1)
    return orders[0] if orders else None


def _buyers() -> list[dict]:
    bs = db.buyers().find({"scenario_id": SCENARIO})
    if not bs:
        seed.seed_buyers()
        bs = db.buyers().find({"scenario_id": SCENARIO})
    return sorted(bs, key=lambda b: b.get("priority", 99))


async def build_allocation(trigger: str = "initial") -> dict:
    """One full Merchant cycle. Returns the written market_orders document."""
    workflow.tracker.start(trigger)
    events.emit("DETECT", f"Merchant cycle starting (trigger={trigger})")

    # ── STAGE 1: READ ARRIVING SUPPLY (transport_plans × farms) ────────
    workflow.tracker.set("read", "running", message="Reading committed plan + farms…")
    plan = latest_committed_plan()
    farms = db.farms().find({"scenario_id": SCENARIO})
    supply_info = agent.arriving_supply(plan, farms)
    supply = supply_info["supply"]
    workflow.tracker.set("read", "done",
                         message=f"{len(supply_info['served_farms'])} farms routed · "
                                 f"{len(supply)} crops arriving",
                         data={"supply": supply,
                               "served_farms": supply_info["served_farms"],
                               "cut_off": [f["farm_id"] for f in supply_info["cut_off_farms"]]})
    events.emit("REASON", f"Supply: {sum(supply.values()):.0f}kg across {len(supply)} crops "
                          f"from {len(supply_info['served_farms'])} routed farms"
                          + (f" (plan {plan['_id']})" if plan else " (no plan yet)"))
    await asyncio.sleep(STAGE_PAUSE)

    # ── STAGE 2: DEMAND BOOK ───────────────────────────────────────────
    workflow.tracker.set("demand", "running", message="Loading buyer standing orders…")
    buyers = _buyers()
    total_demand = sum(d.get("requested_kg", 0) for b in buyers for d in b.get("demand", []))
    workflow.tracker.set("demand", "done",
                         message=f"{len(buyers)} buyers · {total_demand:.0f}kg requested",
                         data={"buyers": [{"id": b["_id"], "name": b.get("name"),
                                           "priority": b.get("priority")} for b in buyers]})
    events.emit("REASON", f"Demand book: {len(buyers)} buyers wanting {total_demand:.0f}kg")
    await asyncio.sleep(STAGE_PAUSE)

    # ── STAGE 3: ALLOCATE (priority fair-share) ────────────────────────
    workflow.tracker.set("allocate", "running", message="Allocating supply by buyer priority…")
    result = agent.allocate(supply, buyers)
    f = result["fulfillment"]
    workflow.tracker.set("allocate", "done",
                         message=f"{f['fill_rate']*100:.0f}% filled · {f['unmet_kg']:.0f}kg unmet",
                         data={"fulfillment": f, "crop_summary": result["crop_summary"]})
    events.emit("REASON", f"Allocated {f['allocated_kg']:.0f}/{f['requested_kg']:.0f}kg "
                          f"({f['fill_rate']*100:.0f}% fill)")
    await asyncio.sleep(STAGE_PAUSE)

    # ── STAGE 4: SCARCITY PRICING ──────────────────────────────────────
    workflow.tracker.set("price", "running", message="Pricing shortfall…")
    scarce = result["scarce_crops"]
    max_mult = max([c["scarcity_multiplier"] for c in result["crop_summary"]], default=1.0)
    workflow.tracker.set("price", "done",
                         message=(f"scarcity on {', '.join(scarce)} (×{max_mult:.2f})" if scarce
                                  else "all crops at baseline price"),
                         data={"scarce_crops": scarce, "max_multiplier": max_mult,
                               "revenue_cents": result["revenue_cents"]})
    events.emit("REASON", f"Pricing: {len(scarce)} scarce crop(s), revenue ${result['revenue_cents']/100:.2f}")
    await asyncio.sleep(STAGE_PAUSE)

    # ── Gemini rationale (off the event loop; graceful fallback) ───────
    loop = asyncio.get_event_loop()
    rationale = await loop.run_in_executor(
        None, lambda: gemini_client.allocation_rationale(result, supply_info, trigger))

    # ── STAGE 5: COMMIT ORDERS ─────────────────────────────────────────
    workflow.tracker.set("commit", "running", message="Writing market_orders…")
    prior = latest_order()
    diff = agent.reallocation_diff(prior, result, supply_info) if (prior and trigger != "initial") else None
    order_id = f"order-{uuid.uuid4().hex[:6]}"
    order = {
        "_id": order_id,
        "scenario_id": SCENARIO,
        "status": "committed",
        "from_plan": plan["_id"] if plan else None,
        "trigger": trigger,
        "world_event_id": (_active_storm() or {}).get("_id"),
        "supply": supply,
        "served_farms": supply_info["served_farms"],
        "cut_off_farms": supply_info["cut_off_farms"],
        "allocations": result["lines"],
        "crop_summary": result["crop_summary"],
        "fulfillment": result["fulfillment"],
        "pricing": {"scarce_crops": scarce, "max_multiplier": max_mult},
        "revenue_cents": result["revenue_cents"],
        "reasoning": {
            "strategy": "priority fair-share; essentials first, then institutional, retail, export; "
                        "shortfall lifts unit price equally for all buyers of a scarce crop",
            "rationale": rationale["text"],
            "gemini_source": rationale["source"],
            "gemini_model": rationale["model"],
            "replan_diff": diff,
        },
        "replaces": prior["_id"] if (prior and trigger != "initial") else None,
        "updated_at": time.time(),
        "updated_by": "merchant",
    }
    # Supersede the prior committed order so readers see only the current truth.
    if prior:
        db.market_orders().update_one({"_id": prior["_id"]}, {"$set": {"status": "superseded"}})
    db.market_orders().insert_one(order)
    workflow.tracker.attach_order(order_id, trigger)
    workflow.tracker.set("commit", "done",
                         message=f"{order_id} committed · ${order['revenue_cents']/100:.2f} revenue")
    events.emit("ACT", f"Committed {order_id} ({trigger}) — "
                       f"{f['fill_rate']*100:.0f}% fill, ${order['revenue_cents']/100:.2f} "
                       f"[rationale via {rationale['source']}]")
    return order


async def reallocate(trigger: str = "storm_reallocate") -> dict:
    """Headline path: re-run allocation after the Transport Agent re-plans for the storm."""
    storm = _active_storm()
    events.emit("DETECT", f"Re-plan/storm detected ({storm['_id'] if storm else 'manual'}) — reallocating")
    return await build_allocation(trigger=trigger)


# --------------------------------------------------------------------------- #
# Blackboard watcher — what makes the cascade automatic.
# --------------------------------------------------------------------------- #
_last_plan_id: str | None = None
_last_storm_id: str | None = None
_ever_allocated = False


async def react_to_blackboard() -> dict | None:
    """Detect a new committed transport_plan or a storm-state change and re-run the
    allocation. Called on a short interval by the background watcher in main.py. Returns
    the new order if one was produced, else None.

    The last-seen plan/storm ids are updated every tick so we react exactly once per
    change. `_ever_allocated` distinguishes the first allocation (trigger 'initial') from
    later re-runs ('replan' / 'storm_reallocate')."""
    global _last_plan_id, _last_storm_id, _ever_allocated
    plan = latest_committed_plan()
    storm = _active_storm()
    plan_id = plan["_id"] if plan else None
    storm_id = storm["_id"] if storm else None

    plan_changed = plan_id != _last_plan_id
    storm_changed = storm_id != _last_storm_id
    _last_plan_id = plan_id
    _last_storm_id = storm_id

    # Nothing to allocate yet (no plan committed) — wait for the Transport Agent.
    if plan is None:
        return None

    if not (plan_changed or storm_changed or not _ever_allocated):
        return None

    trigger = "storm_reallocate" if storm_id else ("initial" if not _ever_allocated else "replan")
    try:
        order = await build_allocation(trigger=trigger)
        _ever_allocated = True
        return order
    except Exception as e:
        events.emit("INFO", f"auto-reallocation failed: {type(e).__name__}: {e}")
        return None
