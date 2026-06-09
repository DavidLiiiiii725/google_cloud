"""
Route Optimizer Service — the math engine for the Transport Agent.

The Transport Agent does the *reasoning* (which farms are worth a stop, how to
turn packing/refrigeration into constraints, sanity-checking the result). This
service does the *math*: given a clean, fully-specified problem, it computes the
cost-optimal consolidation of farms into vehicle routes using Google OR-Tools.

The agent calls POST /optimize as a tool. It does NOT decide routes itself.

Run locally:
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8080

Then POST a problem to http://localhost:8080/optimize
"""

import math
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

app = FastAPI(title="Transport Route Optimizer")

# --- Scaling: OR-Tools works in integers, so we scale floats to ints. -------
DIST_SCALE = 1000   # km -> meters (distance precision)
COST_SCALE = 100    # cost units -> "cents" (cost precision)
VOL_SCALE = 1000    # m3 -> liters (volume precision)


# --- Request / response shapes ---------------------------------------------
class Point(BaseModel):
    lat: float
    lng: float


class Vehicle(BaseModel):
    id: str
    capacity_kg: float
    capacity_m3: float
    refrigerated: bool = False
    fixed_cost: float = 50.0          # cost incurred only if the vehicle is used


class Stop(BaseModel):
    farm_id: str
    lat: float
    lng: float
    demand_kg: float
    demand_m3: float
    requires_refrigeration: bool = False


class OptimizeRequest(BaseModel):
    depot: Point                       # where vehicles start and end (e.g. the merchant hub)
    vehicles: list[Vehicle]
    stops: list[Stop]
    cost_per_km: float = 1.0
    # Penalty for leaving a stop unserved. High = "serve everything if possible".
    # The agent can lower this to let the solver drop tiny/far pickups on purpose.
    drop_penalty: float = 100000.0


class RouteStop(BaseModel):
    stop_seq: int
    farm_id: str
    pickup_kg: float
    pickup_m3: float


class VehicleRoute(BaseModel):
    vehicle_id: str
    refrigerated: bool
    used: bool
    route: list[RouteStop]
    load_kg: float
    load_m3: float
    distance_km: float


class OptimizeResponse(BaseModel):
    feasible: bool
    vehicles: list[VehicleRoute]
    unserved_farms: list[str]
    summary: dict


def haversine_km(a: Point, b: Point) -> float:
    """Great-circle distance in km between two lat/lng points."""
    R = 6371.0
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = math.radians(b.lat - a.lat)
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/optimize", response_model=OptimizeResponse)
def optimize(req: OptimizeRequest):
    n_vehicles = len(req.vehicles)
    n_stops = len(req.stops)

    if n_stops == 0:
        return OptimizeResponse(
            feasible=True, vehicles=[], unserved_farms=[],
            summary={"total_vehicles": 0, "total_distance_km": 0.0,
                     "total_cost": 0.0, "served": 0, "dropped": 0},
        )

    # Node 0 = depot. Nodes 1..n_stops = the farms (stop i -> node i+1).
    nodes = [req.depot] + [Point(lat=s.lat, lng=s.lng) for s in req.stops]
    n_nodes = len(nodes)

    # Pre-compute the distance matrix (scaled to integer meters).
    dist = [[int(haversine_km(nodes[i], nodes[j]) * DIST_SCALE)
             for j in range(n_nodes)] for i in range(n_nodes)]

    manager = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, 0)  # all start/end at depot (0)
    routing = pywrapcp.RoutingModel(manager)

    # --- Arc cost = distance * cost_per_km (in scaled cost units) -----------
    def arc_cost(from_index, to_index):
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        km = dist[i][j] / DIST_SCALE
        return int(km * req.cost_per_km * COST_SCALE)

    transit_idx = routing.RegisterTransitCallback(arc_cost)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    # --- Fixed cost per vehicle (charged only if the vehicle is used) -------
    for v in range(n_vehicles):
        routing.SetFixedCostOfVehicle(int(req.vehicles[v].fixed_cost * COST_SCALE), v)

    # --- Capacity dimension: weight (kg) ------------------------------------
    def demand_kg(from_index):
        node = manager.IndexToNode(from_index)
        return 0 if node == 0 else int(req.stops[node - 1].demand_kg)

    kg_idx = routing.RegisterUnaryTransitCallback(demand_kg)
    routing.AddDimensionWithVehicleCapacity(
        kg_idx, 0, [int(v.capacity_kg) for v in req.vehicles], True, "Weight")

    # --- Capacity dimension: volume (m3, scaled to liters) ------------------
    def demand_m3(from_index):
        node = manager.IndexToNode(from_index)
        return 0 if node == 0 else int(req.stops[node - 1].demand_m3 * VOL_SCALE)

    m3_idx = routing.RegisterUnaryTransitCallback(demand_m3)
    routing.AddDimensionWithVehicleCapacity(
        m3_idx, 0, [int(v.capacity_m3 * VOL_SCALE) for v in req.vehicles], True, "Volume")

    # --- Refrigeration as a capacity dimension ------------------------------
    # Cold-requiring stock has a "cold demand" equal to its weight. Refrigerated
    # vehicles have cold capacity = their full capacity; non-refrigerated vehicles
    # have cold capacity = 0, so cold stock physically cannot be placed on them.
    # This is the robust, idiomatic OR-Tools way to enforce the constraint.
    def cold_demand(from_index):
        node = manager.IndexToNode(from_index)
        if node == 0:
            return 0
        stop = req.stops[node - 1]
        return int(stop.demand_kg) if stop.requires_refrigeration else 0

    cold_idx = routing.RegisterUnaryTransitCallback(cold_demand)
    routing.AddDimensionWithVehicleCapacity(
        cold_idx, 0,
        [int(v.capacity_kg) if v.refrigerated else 0 for v in req.vehicles],
        True, "Cold")

    # --- Allow dropping stops (with penalty) so the problem stays solvable --
    penalty = int(req.drop_penalty * COST_SCALE)
    for s_i in range(n_stops):
        routing.AddDisjunction([manager.NodeToIndex(s_i + 1)], penalty)

    # --- Solve --------------------------------------------------------------
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(5)

    solution = routing.SolveWithParameters(params)
    if solution is None:
        return OptimizeResponse(
            feasible=False, vehicles=[],
            unserved_farms=[s.farm_id for s in req.stops],
            summary={"total_vehicles": 0, "total_distance_km": 0.0,
                     "total_cost": 0.0, "served": 0, "dropped": n_stops},
        )

    # --- Extract routes -----------------------------------------------------
    vehicle_routes: list[VehicleRoute] = []
    served_nodes = set()
    total_distance_km = 0.0

    for v in range(n_vehicles):
        idx = routing.Start(v)
        seq = 0
        stops_out: list[RouteStop] = []
        load_kg = load_m3 = veh_dist = 0.0

        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node != 0:  # skip depot
                stop = req.stops[node - 1]
                seq += 1
                stops_out.append(RouteStop(
                    stop_seq=seq, farm_id=stop.farm_id,
                    pickup_kg=stop.demand_kg, pickup_m3=stop.demand_m3))
                load_kg += stop.demand_kg
                load_m3 += stop.demand_m3
                served_nodes.add(node)
            nxt = solution.Value(routing.NextVar(idx))
            veh_dist += dist[manager.IndexToNode(idx)][manager.IndexToNode(nxt)] / DIST_SCALE
            idx = nxt

        used = len(stops_out) > 0
        if used:
            total_distance_km += veh_dist
        vehicle_routes.append(VehicleRoute(
            vehicle_id=req.vehicles[v].id,
            refrigerated=req.vehicles[v].refrigerated,
            used=used, route=stops_out,
            load_kg=round(load_kg, 1), load_m3=round(load_m3, 2),
            distance_km=round(veh_dist, 2)))

    unserved = [req.stops[node - 1].farm_id
                for node in range(1, n_nodes) if node not in served_nodes]

    total_cost = (total_distance_km * req.cost_per_km
                  + sum(req.vehicles[v].fixed_cost
                        for v, vr in enumerate(vehicle_routes) if vr.used))

    return OptimizeResponse(
        feasible=True,
        vehicles=vehicle_routes,
        unserved_farms=unserved,
        summary={
            "total_vehicles": sum(1 for vr in vehicle_routes if vr.used),
            "total_distance_km": round(total_distance_km, 2),
            "total_cost": round(total_cost, 2),
            "served": len(served_nodes),
            "dropped": len(unserved),
        },
    )
