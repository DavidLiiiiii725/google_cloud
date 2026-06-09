"""OR-Tools route optimizer — standalone Cloud Run service.

The Transport Agent POSTs a formulated problem (nodes, vehicles, cost model) and gets
back routed vehicles + cost. This service does ONLY math — no reasoning, no MongoDB.
That division is the answer to "why an agent and not just a solver?": the agent reasons
(what can co-load, what to optimize for, is the result sane), this computes the routes.

This uses a REAL OR-Tools RoutingModel — a capacitated vehicle routing problem (CVRP)
solved separately per compatibility class (refrigerated vs ambient), because cold-chain
loads can't share a vehicle with ambient loads. Within each class the solver does proper
multi-stop routing: it decides how many vehicles to use, which farms each visits, and in
what order, minimizing fixed-vehicle + distance cost.
"""
from __future__ import annotations
import math
from fastapi import FastAPI
from pydantic import BaseModel
from ortools.constraint_solver import routing_enums_pb2, pywrapcp
from . import routes_client

app = FastAPI(title="route-optimizer")


class Problem(BaseModel):
    nodes: list[dict]
    vehicles: list[dict] = []
    compatibility_groups: list[dict] = []
    objective: str = ""
    cost_model: dict = {}
    storm: dict | None = None


def _haversine_m(a, b):
    R = 6371000.0
    dlat = math.radians(b["lat"] - a["lat"])
    dlng = math.radians(b["lng"] - a["lng"])
    x = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(a["lat"])) * math.cos(math.radians(b["lat"])) * math.sin(dlng / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(x))


@app.get("/health")
def health():
    return {"ok": True}


def _solve_class(nodes, fleet, refrigerated, cost_model):
    """Solve a CVRP for one compatibility class. Returns (vehicles_out, unrouted, (fixed,dist,refrig))."""
    fixed = cost_model.get("fixed_cents", 15000)
    per_km = cost_model.get("per_km_cents", 120)
    refrig_sur = cost_model.get("refrig_surcharge_cents", 2000)

    if not nodes or not fleet:
        return [], [{"farm_id": n["farm_id"], "reason": "no_capacity"} for n in nodes], (0, 0, 0), "n/a"

    depot = {"lat": sum(n["lat"] for n in nodes) / len(nodes),
             "lng": sum(n["lng"] for n in nodes) / len(nodes)}
    locs = [depot] + nodes
    n_loc = len(locs)
    n_veh = len(fleet)

    dist, dur, matrix_source = routes_client.distance_matrix(locs)
    demands = [0] + [int(n["demand_kg"]) for n in nodes]
    caps = [int(v.get("capacity_kg", 10**9)) for v in fleet]

    mgr = pywrapcp.RoutingIndexManager(n_loc, n_veh, 0)
    routing = pywrapcp.RoutingModel(mgr)

    def dist_cb(i, j):
        return int(dist[mgr.IndexToNode(i)][mgr.IndexToNode(j)] / 1000.0 * per_km)
    transit_idx = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)
    for v in range(n_veh):
        routing.SetFixedCostOfVehicle(fixed, v)

    def demand_cb(i):
        return demands[mgr.IndexToNode(i)]
    demand_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(demand_idx, 0, caps, True, "Capacity")

    # Dropping a node must be a last resort: the penalty has to dominate any possible
    # routing detour, so the solver only drops farms that are genuinely infeasible
    # (over total capacity), never ones it could serve. Scale it well above the worst-
    # case round trip plus a full vehicle's fixed cost.
    max_arc = max((dist[i][j] for i in range(n_loc) for j in range(n_loc)), default=0)
    penalty = int(max_arc / 1000.0 * per_km) * n_loc * 4 + fixed * 4 + 10**6
    for node in range(1, n_loc):
        routing.AddDisjunction([mgr.NodeToIndex(node)], penalty)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(5)

    sol = routing.SolveWithParameters(params)
    if not sol:
        return [], [{"farm_id": n["farm_id"], "reason": "no_capacity"} for n in nodes], (0, 0, 0), matrix_source

    vehicles_out, total_fixed, total_dist, total_refrig = [], 0, 0, 0
    routed_nodes = set()
    for v in range(n_veh):
        start = routing.Start(v)
        if routing.IsEnd(sol.Value(routing.NextVar(start))):
            continue
        stops, seq = [], 0
        prev = start
        idx = sol.Value(routing.NextVar(start))
        veh_dist = 0
        veh_dur = 0
        while not routing.IsEnd(idx):
            node = mgr.IndexToNode(idx)
            farm = nodes[node - 1]
            leg_m = dist[mgr.IndexToNode(prev)][node]
            leg_s = dur[mgr.IndexToNode(prev)][node]
            veh_dist += leg_m
            veh_dur += leg_s
            stops.append({"seq": seq, "farm_id": farm["farm_id"],
                          "eta": farm.get("ready_at", 0),
                          "leg_distance_m": leg_m, "leg_duration_s": leg_s,
                          "pickups": [{"sku": f"{farm['farm_id']}-load", "quantity_kg": farm["demand_kg"]}]})
            routed_nodes.add(node)
            seq += 1
            prev = idx
            idx = sol.Value(routing.NextVar(idx))
        veh_dist += dist[mgr.IndexToNode(prev)][mgr.IndexToNode(idx)]
        veh_dur += dur[mgr.IndexToNode(prev)][mgr.IndexToNode(idx)]
        total_fixed += fixed
        total_dist += int(veh_dist / 1000.0 * per_km)
        if refrigerated:
            total_refrig += refrig_sur
        vehicles_out.append({**fleet[v],
                             "load_class": "refrigerated" if refrigerated else "ambient",
                             "route_distance_m": veh_dist, "route_duration_s": veh_dur,
                             "stops": stops})

    unrouted = [{"farm_id": nodes[i - 1]["farm_id"], "reason": "no_capacity"}
                for i in range(1, n_loc) if i not in routed_nodes]
    return vehicles_out, unrouted, (total_fixed, total_dist, total_refrig), matrix_source


@app.post("/solve")
def solve(p: Problem):
    if not p.nodes:
        return {"vehicles": [], "unrouted": [],
                "cost": {"total_cents": 0, "fixed_cents": 0, "distance_cents": 0, "refrigeration_cents": 0}}

    cm = p.cost_model or {}
    refrig_nodes = [n for n in p.nodes if n.get("needs_refrig")]
    ambient_nodes = [n for n in p.nodes if not n.get("needs_refrig")]
    refrig_v = [v for v in p.vehicles if v.get("refrigerated")]
    ambient_v = [v for v in p.vehicles if not v.get("refrigerated")]

    rv, ru, (rf, rd, rr), src_r = _solve_class(refrig_nodes, refrig_v, True, cm)
    av, au, (af, ad, ar), src_a = _solve_class(ambient_nodes, ambient_v, False, cm)

    return {
        "vehicles": rv + av,
        "unrouted": ru + au,
        "matrix_source": src_r if src_r != "n/a" else src_a,
        "cost": {
            "total_cents": rf + rd + rr + af + ad + ar,
            "fixed_cents": rf + af,
            "distance_cents": rd + ad,
            "refrigeration_cents": rr + ar,
        },
    }
