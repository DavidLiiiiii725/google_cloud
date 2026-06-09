"""Test the optimizer directly (no server) with the seed-data farms."""
from main import optimize, OptimizeRequest, Point, Vehicle, Stop

DEPOT = Point(lat=52.20, lng=0.12)  # merchant / distribution hub

VEHICLES = [
    Vehicle(id="reefer_1", capacity_kg=500, capacity_m3=4.0, refrigerated=True, fixed_cost=60),
    Vehicle(id="truck_2",  capacity_kg=700, capacity_m3=4.0, refrigerated=False, fixed_cost=50),
    Vehicle(id="truck_3",  capacity_kg=700, capacity_m3=4.0, refrigerated=False, fixed_cost=50),
]

# Baseline: all 5 farms healthy (matches seed_data.py baseline)
BASELINE = [
    Stop(farm_id="farm_A", lat=52.210, lng=0.140, demand_kg=180, demand_m3=1.2, requires_refrigeration=True),
    Stop(farm_id="farm_B", lat=52.180, lng=0.090, demand_kg=520, demand_m3=0.9, requires_refrigeration=False),
    Stop(farm_id="farm_C", lat=52.230, lng=0.180, demand_kg=140, demand_m3=1.6, requires_refrigeration=True),
    Stop(farm_id="farm_D", lat=52.160, lng=0.210, demand_kg=260, demand_m3=1.4, requires_refrigeration=False),
    Stop(farm_id="farm_E", lat=52.140, lng=0.060, demand_kg=430, demand_m3=0.8, requires_refrigeration=False),
]

# Storm: A cut 30% (->126), B delayed (removed), E gutted to 86kg (likely dropped)
STORM = [
    Stop(farm_id="farm_A", lat=52.210, lng=0.140, demand_kg=126, demand_m3=0.84, requires_refrigeration=True),
    Stop(farm_id="farm_C", lat=52.230, lng=0.180, demand_kg=140, demand_m3=1.6, requires_refrigeration=True),
    Stop(farm_id="farm_D", lat=52.160, lng=0.210, demand_kg=260, demand_m3=1.4, requires_refrigeration=False),
    Stop(farm_id="farm_E", lat=52.140, lng=0.060, demand_kg=86, demand_m3=0.16, requires_refrigeration=False),
]


def show(label, resp):
    print(f"\n{'='*60}\n{label}\n{'='*60}")
    print(f"feasible={resp.feasible}  {resp.summary}")
    for vr in resp.vehicles:
        if vr.used:
            stops = " -> ".join(f"{s.farm_id}({s.pickup_kg}kg)" for s in vr.route)
            print(f"  {vr.vehicle_id} [{'COLD' if vr.refrigerated else 'amb'}] "
                  f"{vr.load_kg}kg {vr.load_m3}m3 {vr.distance_km}km : depot -> {stops} -> depot")
    if resp.unserved_farms:
        print(f"  UNSERVED: {resp.unserved_farms}")


show("BASELINE — 5 farms", optimize(OptimizeRequest(depot=DEPOT, vehicles=VEHICLES, stops=BASELINE, cost_per_km=1.5)))

# Storm with a lower drop penalty so the solver may choose to skip a tiny far pickup
show("STORM — A reduced, B gone, E tiny", optimize(OptimizeRequest(
    depot=DEPOT, vehicles=VEHICLES, stops=STORM, cost_per_km=1.5, drop_penalty=300)))
