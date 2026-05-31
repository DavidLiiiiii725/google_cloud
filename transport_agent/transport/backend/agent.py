"""The reasoning layer of the Transport Agent.

THIS FILE is the agent's brain — the part that makes it an agent and not just a call
to a solver. Everything here is real-world judgment turned into solver-ready structure:
which stock can share a vehicle, how to frame the optimization, whether the returned
plan is sane. The OR-Tools call in optimizer_client.py is the dumb-but-fast math.

The functions below ship with WORKING-DEFAULT implementations so the pipeline runs
end-to-end against the in-memory store today. Replace the bodies marked TODO with your
real rules (and, where useful, a Gemini call mirroring greenhouse/backend/gemini_client.py).
Keep the return SHAPES — pipeline.py and the schema depend on them.
"""
from __future__ import annotations

# Simple cost model (cents). Tune later; never block the demo on tuning.
FIXED_COST_PER_VEHICLE_CENTS = 15000
DISTANCE_COST_PER_KM_CENTS = 120
REFRIG_SURCHARGE_CENTS = 2000


def compatibility_groups(farms: list[dict]) -> list[dict]:
    """Group SKUs across farms into co-loadable sets. Returns the schema's
    reasoning.compatibility_groups shape. Default rule set:
      - refrigerated stock => its own 'cold' group (cold chain separate from ambient)
      - fragile/loose stock => 'ambient-fragile' (not stacked under heavy crates)
      - everything else => 'ambient-general'
    TODO: refine (e.g. odor cross-contamination, weight-on-top limits).
    """
    cold, fragile, general = [], [], []
    for f in farms:
        for s in f.get("stock", []):
            sku = s["sku"]
            if s.get("refrigerated"):
                cold.append(sku)
            elif s.get("fragile") or s.get("packing") == "loose":
                fragile.append(sku)
            else:
                general.append(sku)
    groups = []
    if cold:
        groups.append({"group": "cold", "skus": sorted(set(cold)),
                       "rule": "refrigerated kept separate from ambient"})
    if fragile:
        groups.append({"group": "ambient-fragile", "skus": sorted(set(fragile)),
                       "rule": "loose/fragile not stacked under heavy crates"})
    if general:
        groups.append({"group": "ambient-general", "skus": sorted(set(general)),
                       "rule": "stackable ambient stock"})
    return groups


def formulate(farms: list[dict], groups: list[dict], storm: dict | None) -> dict:
    """Translate farms + compatibility groups + storm conditions into clean optimizer
    inputs: nodes (pickup points), vehicles, and constraints. Returns a dict the
    route-optimizer /solve endpoint understands.

    Each farm's stock is split by compatibility class into SEPARATE nodes — a
    refrigerated node and an ambient node — because cold-chain stock can't ride the
    same vehicle as ambient stock. A farm with both becomes two pickups (same location)
    that the solver can assign to different trucks. This is what lets the cold truck
    consolidate several farms' refrigerated stock into one multi-stop route.
    """
    nodes = []
    for f in farms:
        stock = f.get("stock", [])
        for needs_refrig in (True, False):
            items = [s for s in stock if bool(s.get("refrigerated")) == needs_refrig]
            if not items:
                continue
            qty = sum(s["quantity_kg"] for s in items)
            if qty <= 0:
                continue
            suffix = "-cold" if needs_refrig else "-ambient"
            nodes.append({
                "farm_id": f["_id"] + suffix,
                "base_farm_id": f["_id"],
                "lat": f["location"]["lat"], "lng": f["location"]["lng"],
                "demand_kg": qty,
                "demand_l": sum(s.get("volume_l", 0) for s in items),
                "needs_refrig": needs_refrig,
                "ready_at": min((s.get("ready_at", 0) for s in items), default=0),
                "deadline_at": min((s.get("deadline_at", 1e18) for s in items), default=1e18),
                "access": f.get("access", "open"),
            })
    # Minimal default fleet. Ambient trucks sized so nearby farms can consolidate into
    # multi-stop routes (the core value prop); the storm is what later forces them apart.
    vehicles = [
        {"vehicle_id": "veh-refrig-01", "capacity_kg": 1200, "capacity_l": 1400, "refrigerated": True},
        {"vehicle_id": "veh-ambient-01", "capacity_kg": 1500, "capacity_l": 1700, "refrigerated": False},
        {"vehicle_id": "veh-ambient-02", "capacity_kg": 1500, "capacity_l": 1700, "refrigerated": False},
    ]
    return {
        "nodes": nodes,
        "vehicles": vehicles,
        "compatibility_groups": groups,
        "objective": "minimize fixed + distance cost subject to capacity, cold-chain, fragility, time windows",
        "cost_model": {
            "fixed_cents": FIXED_COST_PER_VEHICLE_CENTS,
            "per_km_cents": DISTANCE_COST_PER_KM_CENTS,
            "refrig_surcharge_cents": REFRIG_SURCHARGE_CENTS,
        },
        "storm": storm,
    }


def sanity_checks(solution: dict, farms: list[dict]) -> list[dict]:
    """Validate the optimizer output before committing. Returns the schema's
    reasoning.sanity_checks shape. Catches the nonsense the prompt names:
    perishables past deadline, near-empty extra trucks. TODO: add more.
    """
    checks = []

    # 1) No perishable load arriving after its deadline.
    deadline_by_farm = {
        f["_id"]: min((s.get("deadline_at", 1e18) for s in f.get("stock", []) if s.get("perishable")),
                      default=1e18)
        for f in farms
    }
    late = []
    for v in solution.get("vehicles", []):
        for stop in v.get("stops", []):
            dl = deadline_by_farm.get(stop["farm_id"], 1e18)
            if stop.get("eta", 0) > dl:
                late.append(stop["farm_id"])
    checks.append({"check": "no perishable past deadline", "passed": not late,
                   "note": "all ETAs within deadline" if not late else f"late: {late}"})

    # 2) No near-empty extra vehicle (fixed cost not justified).
    near_empty = []
    for v in solution.get("vehicles", []):
        load = sum(p["quantity_kg"] for stop in v.get("stops", []) for p in stop.get("pickups", []))
        cap = v.get("capacity_kg", 1) or 1
        if v.get("stops") and load / cap < 0.25:
            near_empty.append(v["vehicle_id"])
    checks.append({"check": "no near-empty extra vehicle", "passed": not near_empty,
                   "note": "all vehicles >25% utilized" if not near_empty else f"underused: {near_empty}"})

    return checks


def replan_diff(prior: dict, solution: dict, blocked: list[dict]) -> dict:
    """Build the before/after the judge sees. prior is the superseded plan doc."""
    def count_stops(plan_vehicles):
        return sum(len(v.get("stops", [])) for v in plan_vehicles)
    pv = prior.get("vehicles", [])
    nv = solution.get("vehicles", [])
    summary_bits = []
    if len(pv) != len(nv):
        summary_bits.append(f"{len(pv)} trucks → {len(nv)}")
    if blocked:
        summary_bits.append(f"{', '.join(b['_id'] for b in blocked)} unreachable, dropped")
    return {
        "stops_before": count_stops(pv), "stops_after": count_stops(nv),
        "vehicles_before": len(pv), "vehicles_after": len(nv),
        "cost_before_cents": prior.get("cost", {}).get("total_cents", 0),
        "cost_after_cents": solution.get("cost", {}).get("total_cents", 0),
        "summary": "; ".join(summary_bits) or "routes adjusted",
    }


def greedy_fallback(problem: dict) -> dict:
    """If the OR-Tools service is unreachable, produce a valid (not optimal) plan so the
    demo never crashes. One vehicle per compatibility need, naive assignment.
    """
    nodes = problem["nodes"]
    refrig_nodes = [n for n in nodes if n["needs_refrig"]]
    ambient_nodes = [n for n in nodes if not n["needs_refrig"]]
    vehicles_out = []
    cm = problem["cost_model"]
    total_fixed = 0

    def pack(node_list, vehicle):
        nonlocal total_fixed
        if not node_list:
            return
        total_fixed += cm["fixed_cents"]
        stops = [{"seq": i, "farm_id": n["farm_id"], "eta": n["ready_at"],
                  "pickups": [{"sku": f"{n['farm_id']}-load", "quantity_kg": n["demand_kg"]}]}
                 for i, n in enumerate(node_list)]
        vehicles_out.append({**vehicle,
                             "load_class": "refrigerated" if vehicle["refrigerated"] else "ambient",
                             "stops": stops})

    fleet = problem["vehicles"]
    refrig_v = next((v for v in fleet if v["refrigerated"]), None)
    ambient_v = next((v for v in fleet if not v["refrigerated"]), None)
    if refrig_v:
        pack(refrig_nodes, refrig_v)
    if ambient_v:
        pack(ambient_nodes, ambient_v)

    return {
        "vehicles": vehicles_out,
        "unrouted": [],
        "matrix_source": "haversine",
        "cost": {"total_cents": total_fixed, "fixed_cents": total_fixed,
                 "distance_cents": 0, "refrigeration_cents": 0},
    }
