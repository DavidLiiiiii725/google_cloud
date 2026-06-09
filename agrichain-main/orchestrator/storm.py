#!/usr/bin/env python3
"""
Orchestrator — fires the three agents in sequence and captures the storm cascade.

The whole demo in one run:
    NORMAL DAY  -> transport plans baseline farms, merchant places orders   (BEFORE)
    STORM HITS  -> farmer agents adapt greenhouses + revise yields
    RE-PLAN     -> transport re-plans, merchant re-allocates                 (AFTER)
    DIFF        -> prints before-vs-after: trucks, cost, served/dropped

Two modes:
  --simulate  (default) The three steps are fulfilled by built-in stubs so the
              FULL cascade runs today, with no teammate agents required. The
              transport stub calls your REAL OR-Tools optimizer if it's running.
  --live      Each step calls the real agent via the trigger_* hooks below
              (your teammates fill these in), then polls MongoDB until the
              agent's document appears.

This decoupling is deliberate: the orchestrator coordinates through the shared
`agrichain` database, so it works even if a teammate's agent is late — and the
demo degrades gracefully (a stub stands in for any missing agent).

    export MDB_MCP_CONNECTION_STRING="<your working mongodb:// string>"
    python orchestrator.py            # simulate — runs the whole cascade now
    python orchestrator.py --live     # use real agents via the hooks
"""

import argparse
import os
import sys
import time

try:
    from pymongo import MongoClient
except ImportError:
    sys.exit("pymongo not installed. Run:  pip install pymongo")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import requests  # only needed for the optimizer call / live HTTP triggers
except ImportError:
    requests = None

CONN = os.environ.get("MDB_MCP_CONNECTION_STRING") or os.environ.get("MONGODB_URI")
DB_NAME = "agrichain"
OPTIMIZER_URL = os.environ.get("OPTIMIZER_URL", "http://localhost:8080")

DEPOT = {"lat": 52.20, "lng": 0.12}
FLEET = [
    {"id": "reefer_1", "capacity_kg": 500, "capacity_m3": 4.0, "refrigerated": True,  "fixed_cost": 60},
    {"id": "truck_2",  "capacity_kg": 700, "capacity_m3": 4.0, "refrigerated": False, "fixed_cost": 50},
    {"id": "truck_3",  "capacity_kg": 700, "capacity_m3": 4.0, "refrigerated": False, "fixed_cost": 50},
]


# ===========================================================================
#  LIVE TRIGGERS — used in --live mode. Signatures match the sim stubs so the
#  run() loop calls them identically regardless of mode.
#
#  Design note: the transport and farmer steps run deterministic logic (real
#  optimizer / real storm revisions) so the recorded cascade fires reliably
#  every time. The real LLM-driven ADK agents are demoed SEPARATELY to show the
#  reasoning layer — everything is real, the non-deterministic LLM just isn't
#  chained into the one-take cascade. The merchant step calls the teammate's
#  real FastAPI endpoint over HTTP.
# ===========================================================================
MERCHANT_URL = os.environ.get("MERCHANT_URL", "http://localhost:8000")


def trigger_farmer_agents(db, storm: bool):
    """Apply the storm revisions to `farms` (deterministic).
    The real Farmer ADK agent is shown separately in the demo."""
    return sim_farmer(db, storm)


def trigger_transport_agent(db, version, replan_reason=""):
    """Run the real transport logic: farm-selection rules + the REAL OR-Tools
    optimizer service, writing the plan to `transport_plans`. Deterministic so
    the cascade is reliable. The full Gemini+MCP ADK transport agent is demoed
    separately to showcase the reasoning layer."""
    return sim_transport(db, version, replan_reason)


def trigger_merchant_agent(db, version):
    """Trigger the teammate's Merchant agent over HTTP. Their FastAPI endpoint
    must read `transport_plans` and write `orders` in the shared agrichain DB."""
    if requests is None:
        sys.exit("`requests` needed for the merchant HTTP trigger. pip install requests")
    try:
        resp = requests.post(
            f"{MERCHANT_URL}/merchant/allocate",
            json={"version": version},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        sys.exit(f"Could not reach merchant agent at {MERCHANT_URL} ({e}).\n"
                 f"Make sure their app is running and the /merchant/allocate "
                 f"endpoint exists, OR run in --simulate mode.")


# ===========================================================================
#  SIMULATE STUBS — let the whole cascade run today with no real agents.
#  Swap these out for the live triggers above as each agent comes online.
# ===========================================================================
def sim_farmer(db, storm: bool):
    """Stand-in for the Farmer agents: on storm, revise yields + flag a road."""
    if not storm:
        return
    # farm_A: greenhouse partly protected, 30% loss on fragile strawberries
    a = db.farms.find_one({"farm_id": "farm_A"})
    if a:
        orig = a["stock"]["quantity_kg"]
        db.farms.update_one({"farm_id": "farm_A"}, {"$set": {
            "stock.quantity_kg": round(orig * 0.70),
            "storm_impact": {"affected": True, "yield_change_pct": -30,
                             "revised_from_quantity_kg": orig}}})
    # farm_B: harvest delayed — misses this run
    db.farms.update_one({"farm_id": "farm_B"}, {"$set": {
        "status": "delayed",
        "storm_impact": {"affected": True, "yield_change_pct": 0,
                         "revised_from_quantity_kg": None}}})
    # farm_E: yield gutted
    e = db.farms.find_one({"farm_id": "farm_E"})
    if e:
        orig = e["stock"]["quantity_kg"]
        db.farms.update_one({"farm_id": "farm_E"}, {"$set": {
            "stock.quantity_kg": round(orig * 0.20),
            "storm_impact": {"affected": True, "yield_change_pct": -80,
                             "revised_from_quantity_kg": orig}}})
    # road R12 flooded
    db.road_conditions.update_one({"segment": "R12"},
                                  {"$set": {"passable": False, "delay_min": 999}})


def sim_transport(db, version: int, replan_reason: str = ""):
    """Stand-in for the Transport agent: applies the agent's selection rules,
    then calls the REAL optimizer service for the routing math."""
    farms = list(db.farms.find())
    roads = {r["segment"]: r for r in db.road_conditions.find()}

    # --- the selection reasoning the real agent does ---
    stops, excluded = [], []
    for f in farms:
        fid, s = f["farm_id"], f["stock"]
        if f.get("status") != "ready":
            excluded.append((fid, f"status={f.get('status')}"))
            continue
        seg = f.get("road_segment")
        if seg and seg in roads and not roads[seg]["passable"]:
            excluded.append((fid, f"road {seg} impassable"))
            continue
        if s["quantity_kg"] < 100:  # tiny pickup not worth a dedicated stop
            excluded.append((fid, f"yield {s['quantity_kg']}kg too small"))
            continue
        stops.append({"farm_id": fid, "lat": f["location"]["lat"], "lng": f["location"]["lng"],
                      "demand_kg": s["quantity_kg"], "demand_m3": s["volume_m3"],
                      "requires_refrigeration": s["requires_refrigeration"]})

    # --- the math (real OR-Tools service) ---
    if requests is None:
        sys.exit("`requests` needed for the optimizer call. pip install requests")
    try:
        r = requests.post(f"{OPTIMIZER_URL}/optimize", timeout=30, json={
            "depot": DEPOT, "vehicles": FLEET, "stops": stops, "cost_per_km": 1.5})
        r.raise_for_status()
        opt = r.json()
    except Exception as e:
        sys.exit(f"Could not reach optimizer at {OPTIMIZER_URL} ({e}).\n"
                 f"Start it:  cd route-optimizer && uvicorn main:app --port 8080")

    plan = {
        "_id": f"plan_{version}",
        "plan_version": version,
        "replanned_from": f"plan_{version-1}" if version > 1 else None,
        "replan_reason": replan_reason,
        "vehicles": [v for v in opt["vehicles"] if v["used"]],
        "unserved_farms": [{"farm_id": fid, "reason": why} for fid, why in excluded]
                          + [{"farm_id": fid, "reason": "optimizer dropped"} for fid in opt["unserved_farms"]],
        "summary": opt["summary"],
    }
    db.transport_plans.replace_one({"_id": plan["_id"]}, plan, upsert=True)
    return plan


def sim_merchant(db, version: int):
    """Stand-in for the Merchant agent: turn the latest plan into orders, with a
    simple shortfall-aware pricing bump."""
    plan = db.transport_plans.find_one({"plan_version": version})
    if not plan:
        return
    served_kg = sum(s["pickup_kg"] for v in plan["vehicles"] for s in v["route"])
    # crude scarcity pricing: less supply -> higher unit price
    base_price = 2.0
    unit_price = round(base_price * (1.0 + max(0, (1200 - served_kg)) / 3000), 2)
    db.orders.replace_one({"_id": f"orders_{version}"}, {
        "_id": f"orders_{version}",
        "from_plan": plan["_id"],
        "total_supply_kg": served_kg,
        "unit_price": unit_price,
        "note": "scarcity pricing applied" if unit_price > base_price else "normal pricing",
    }, upsert=True)


# ===========================================================================
#  Orchestration
# ===========================================================================
def connect():
    if not CONN:
        sys.exit("Set MDB_MCP_CONNECTION_STRING to your Atlas connection string first.")
    db = MongoClient(CONN)[DB_NAME]
    db.command("ping")
    return db


def wait_for(check, what, timeout=60):
    """Poll until `check()` is truthy (used in live mode to wait on an agent)."""
    start = time.time()
    while time.time() - start < timeout:
        if check():
            return True
        time.sleep(2)
    sys.exit(f"Timed out waiting for: {what}")


def reset_baseline(db):
    """Reset to a clean pre-storm state via the seed script's baseline.
    seed_data.py lives at the repo root; this file lives in orchestrator/,
    so look one level up. Falls back to a couple of other likely spots."""
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "seed_data.py"),            # repo root (one up)
        os.path.join(here, "seed_data.py"),                  # same folder
        os.path.join(here, "..", "shared", "seed_data.py"),  # shared/ (if moved later)
    ]
    seed = next((os.path.abspath(p) for p in candidates if os.path.exists(p)), None)
    if seed:
        subprocess.run([sys.executable, seed, "--reset"], check=True)
    else:
        print("  (seed_data.py not found — assuming baseline already loaded; "
              "run `python seed_data.py --reset` manually if needed)")
    db.transport_plans.delete_many({})
    db.orders.delete_many({})


def snapshot(db, version):
    plan = db.transport_plans.find_one({"plan_version": version}) or {}
    order = db.orders.find_one({"_id": f"orders_{version}"}) or {}
    return {"summary": plan.get("summary", {}),
            "unserved": [u["farm_id"] for u in plan.get("unserved_farms", [])],
            "unit_price": order.get("unit_price")}


def run(simulate: bool):
    db = connect()
    # Both modes now share signatures, so the cascade logic is identical.
    farmer    = sim_farmer    if simulate else trigger_farmer_agents
    transport = sim_transport if simulate else trigger_transport_agent
    merchant  = sim_merchant  if simulate else trigger_merchant_agent
    mode = "SIMULATE" if simulate else "LIVE"
    print(f"\n=== Running storm cascade in {mode} mode ===")

    print("\n[0] Reset to baseline (pre-storm)…")
    reset_baseline(db)

    print("[1] NORMAL DAY — transport plans baseline, merchant orders…")
    transport(db, version=1, replan_reason="normal day")
    if not simulate:
        wait_for(lambda: db.transport_plans.find_one({"plan_version": 1}), "transport plan v1")
    merchant(db, version=1)
    if not simulate:
        wait_for(lambda: db.orders.find_one({"_id": "orders_1"}), "orders v1")
    before = snapshot(db, 1)

    print("[2] STORM — farmer agents adapt greenhouses & revise yields…")
    farmer(db, storm=True)
    if not simulate:
        wait_for(lambda: db.farms.find_one({"storm_impact.affected": True}), "revised farms")

    print("[3] RE-PLAN — transport re-routes, merchant re-allocates…")
    transport(db, version=2, replan_reason="storm: yields revised, R12 impassable")
    if not simulate:
        wait_for(lambda: db.transport_plans.find_one({"plan_version": 2}), "transport plan v2")
    merchant(db, version=2)
    if not simulate:
        wait_for(lambda: db.orders.find_one({"_id": "orders_2"}), "orders v2")
    after = snapshot(db, 2)

    print_diff(before, after)


def print_diff(before, after):
    bs, as_ = before["summary"], after["summary"]
    print("\n" + "=" * 58)
    print("  STORM CASCADE — BEFORE vs AFTER")
    print("=" * 58)
    print(f"  {'metric':22}{'before':>12}{'after':>12}")
    print("  " + "-" * 46)
    rows = [
        ("trucks used",      bs.get("total_vehicles"), as_.get("total_vehicles")),
        ("total cost",       bs.get("total_cost"),     as_.get("total_cost")),
        ("farms served",     bs.get("served"),         as_.get("served")),
        ("farms dropped",    bs.get("dropped"),        as_.get("dropped")),
        ("unit price",       before.get("unit_price"), after.get("unit_price")),
    ]
    for name, b, a in rows:
        print(f"  {name:22}{str(b):>12}{str(a):>12}")
    if after["unserved"]:
        print(f"\n  after-storm unserved farms: {after['unserved']}")
    print("=" * 58 + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Run the storm-cascade orchestration.")
    p.add_argument("--live", action="store_true",
                   help="use real agents via trigger_* hooks (default is --simulate)")
    args = p.parse_args()
    run(simulate=not args.live)
