"""Transport Agent pipeline: read farms → reason compatibility → formulate →
optimize (OR-Tools) → sanity-check → write transport_plan.

This is the spine of your agent. It mirrors greenhouse/backend/pipeline.py's shape:
each stage reports to workflow.tracker and emits an events log line, with small pauses
so a human audience can watch the reasoning light up. The headline path is replan(),
which re-runs the same spine after a storm and writes a NEW plan tagged replan_of the
prior one, with a populated reasoning.replan_diff (the before/after the judge sees).

Stubs marked TODO are where your real logic goes; the structure, tracing, and MongoDB
read/write contract are complete and runnable against the in-memory store today.
"""
from __future__ import annotations
import asyncio
import time
import uuid
from . import db, events, workflow, agent, optimizer_client

STAGE_PAUSE = 0.4
SCENARIO = "default"


def _active_storm() -> dict | None:
    return db.world_events().find_one({"scenario_id": SCENARIO, "type": "storm", "status": "active"})


def _latest_committed_plan() -> dict | None:
    plans = db.transport_plans().find(
        {"scenario_id": SCENARIO, "status": "committed"}, sort=[("updated_at", -1)], limit=1)
    return plans[0] if plans else None


async def build_plan(trigger: str = "initial", world_event_id: str | None = None) -> dict:
    """One full Transport cycle. Returns the written transport_plan document."""
    workflow.tracker.start(trigger)
    events.emit("DETECT", f"Transport cycle starting (trigger={trigger})")

    # ── STAGE 1: READ FARMS (MongoDB MCP) ──────────────────────────────
    workflow.tracker.set("read", "running", message="Reading farms from MongoDB…")
    farms = db.farms().find({"scenario_id": SCENARIO})
    servable = [f for f in farms if f.get("access") != "blocked" and f.get("stock")]
    blocked = [f for f in farms if f.get("access") == "blocked"]
    workflow.tracker.set("read", "done",
                         message=f"{len(servable)} servable farms, {len(blocked)} blocked",
                         data={"servable": [f["_id"] for f in servable],
                               "blocked": [f["_id"] for f in blocked]})
    events.emit("REASON", f"Read {len(farms)} farms · {len(servable)} servable · {len(blocked)} blocked")
    await asyncio.sleep(STAGE_PAUSE)

    # ── STAGE 2: COMPATIBILITY REASONING ───────────────────────────────
    workflow.tracker.set("compat", "running", message="Grouping co-loadable stock…")
    groups = agent.compatibility_groups(servable)   # TODO: your real rules
    workflow.tracker.set("compat", "done",
                         message=f"{len(groups)} load groups",
                         data={"compatibility_groups": groups})
    events.emit("REASON", f"Compatibility: {len(groups)} groups "
                          f"({', '.join(g['group'] for g in groups)})")
    await asyncio.sleep(STAGE_PAUSE)

    # ── STAGE 3: FORMULATE OPTIMIZER INPUTS ────────────────────────────
    workflow.tracker.set("formulate", "running", message="Translating constraints to solver inputs…")
    problem = agent.formulate(servable, groups, storm=_active_storm())   # TODO
    workflow.tracker.set("formulate", "done",
                         message=f"{len(problem['nodes'])} nodes · {len(problem['vehicles'])} vehicles",
                         data={"objective": problem.get("objective"),
                               "n_nodes": len(problem["nodes"]),
                               "n_vehicles": len(problem["vehicles"])})
    await asyncio.sleep(STAGE_PAUSE)

    # ── STAGE 4: OPTIMIZE (OR-Tools on Cloud Run) ──────────────────────
    workflow.tracker.set("optimize", "running", message="Calling OR-Tools route optimizer…")
    try:
        solution = await optimizer_client.solve(problem)   # HTTP to route-optimizer service
    except Exception as e:
        workflow.tracker.set("optimize", "failed", message=f"{type(e).__name__}: {e}")
        events.emit("REASON", f"Optimizer failed — {type(e).__name__}; using greedy fallback")
        solution = agent.greedy_fallback(problem)           # graceful degradation
    workflow.tracker.set("optimize", "done",
                         message=f"{len(solution['vehicles'])} vehicles routed",
                         data={"vehicles": len(solution["vehicles"]),
                               "total_cents": solution["cost"]["total_cents"]})
    events.emit("REASON", f"Optimizer: {len(solution['vehicles'])} vehicles · "
                          f"{solution['cost']['total_cents']}c")
    await asyncio.sleep(STAGE_PAUSE)

    # ── STAGE 5: SANITY CHECK ──────────────────────────────────────────
    workflow.tracker.set("sanity", "running", message="Validating plan before commit…")
    checks = agent.sanity_checks(solution, servable)        # TODO: deadline / empty-truck checks
    all_pass = all(c["passed"] for c in checks)
    workflow.tracker.set("sanity", "done" if all_pass else "waiting",
                         message="all checks passed" if all_pass else "issues flagged",
                         data={"sanity_checks": checks})
    events.emit("REASON", f"Sanity: {sum(c['passed'] for c in checks)}/{len(checks)} passed")
    await asyncio.sleep(STAGE_PAUSE)

    # ── WRITE PLAN ─────────────────────────────────────────────────────
    plan_id = f"plan-{uuid.uuid4().hex[:6]}"
    prior = _latest_committed_plan() if trigger == "storm_replan" else None
    plan = {
        "_id": plan_id,
        "scenario_id": SCENARIO,
        "status": "committed",
        "replan_of": prior["_id"] if prior else None,
        "trigger": trigger,
        "world_event_id": world_event_id,
        "vehicles": solution["vehicles"],
        "unrouted": solution.get("unrouted", []) + [
            {"farm_id": f["_id"], "reason": "access_blocked"} for f in blocked],
        "cost": solution["cost"],
        "reasoning": {
            "compatibility_groups": groups,
            "objective": problem.get("objective", "minimize fixed + distance cost"),
            "sanity_checks": checks,
            "replan_diff": agent.replan_diff(prior, solution, blocked) if prior else None,
        },
        "updated_at": time.time(),
        "updated_by": "transport",
    }
    # Supersede the prior plan so the Merchant reads only the current truth.
    if prior:
        db.transport_plans().update_one({"_id": prior["_id"]}, {"$set": {"status": "superseded"}})
    db.transport_plans().insert_one(plan)
    workflow.tracker.attach_plan(plan_id, trigger)
    events.emit("ACT", f"Committed {plan_id} ({trigger}) — "
                       f"{len(plan['vehicles'])} vehicles, {len(plan['unrouted'])} unrouted")
    return plan


async def replan(world_event_id: str | None = None) -> dict:
    """Headline path: re-plan after a storm. Same spine, tagged as a re-plan with a diff."""
    storm = _active_storm()
    events.emit("DETECT", f"Storm detected ({storm['_id'] if storm else 'manual'}) — re-planning")
    return await build_plan(trigger="storm_replan",
                            world_event_id=world_event_id or (storm["_id"] if storm else None))
