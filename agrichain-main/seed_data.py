#!/usr/bin/env python3
"""
seed_data.py — Seed the shared Atlas cluster for the agri multi-agent project.

Drops realistic `farms` and `road_conditions` documents so the Transport Agent
can be built and demoed before the Farmer Agent exists.

Two scenarios:
  baseline : 5 healthy farms, all roads passable        (the "before" state)
  storm    : storm-revised yields + one impassable road  (the "after" state)

Usage:
  export MDB_MCP_CONNECTION_STRING="mongodb+srv://<user>:<pass>@<cluster>/agrichain"
  python seed_data.py                # seed baseline (default)
  python seed_data.py --storm        # seed the storm scenario
  python seed_data.py --reset        # wipe collections, then seed baseline
  python seed_data.py --show         # print current farms (Day 1 smoke-test helper)

Install once:  pip install pymongo
"""

import argparse
import os
import sys
from datetime import datetime, timezone

try:
    from pymongo import MongoClient
except ImportError:
    sys.exit("pymongo not installed. Run:  pip install pymongo")

try:
    from dotenv import load_dotenv
    _here = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(_here if os.path.exists(_here) else None)
except ImportError:
    pass
# Same env var the MCP server uses, so the whole team stays consistent.
CONN = os.environ.get("MDB_MCP_CONNECTION_STRING") or os.environ.get("MONGODB_URI")
DB_NAME = "agrichain"

NOW = datetime.now(timezone.utc).isoformat()


# --- BASELINE: 5 farms with deliberately varied stock ----------------------
# The variety is the point: it forces the Transport Agent to reason about
# refrigerated-vs-ambient and fragile-vs-sturdy when forming load groups.
def baseline_farms():
    return [
        {
            "_id": "farm_A_batch_0612",
            "farm_id": "farm_A",
            "farm_name": "Green Valley Greenhouse",
            "location": {"lat": 52.210, "lng": 0.140, "address": "Green Valley, North Rd"},
            "stock": {
                "crop": "strawberries",
                "quantity_kg": 180,
                "volume_m3": 1.2,
                "packing": "loose_crate",
                "fragile": True,
                "requires_refrigeration": True,
                "max_stack_weight_kg": 20,
            },
            "harvest_ready_date": "2026-06-12T06:00:00Z",
            "perishability_hours": 48,
            "pickup_window": {"earliest": "06:00", "latest": "11:00"},
            "road_segment": "R12",
            "status": "ready",
            "storm_impact": {"affected": False, "yield_change_pct": 0, "revised_from_quantity_kg": None},
            "updated_at": NOW,
        },
        {
            "_id": "farm_B_batch_0612",
            "farm_id": "farm_B",
            "farm_name": "Hilltop Roots",
            "location": {"lat": 52.180, "lng": 0.090, "address": "Hilltop Farm Lane"},
            "stock": {
                "crop": "potatoes",
                "quantity_kg": 520,
                "volume_m3": 0.9,
                "packing": "sack",
                "fragile": False,
                "requires_refrigeration": False,
                "max_stack_weight_kg": 300,
            },
            "harvest_ready_date": "2026-06-12T05:00:00Z",
            "perishability_hours": 336,
            "pickup_window": {"earliest": "05:00", "latest": "14:00"},
            "road_segment": "R7",
            "status": "ready",
            "storm_impact": {"affected": False, "yield_change_pct": 0, "revised_from_quantity_kg": None},
            "updated_at": NOW,
        },
        {
            "_id": "farm_C_batch_0612",
            "farm_id": "farm_C",
            "farm_name": "Riverside Leaf Co.",
            "location": {"lat": 52.230, "lng": 0.180, "address": "Riverside Greenhouses"},
            "stock": {
                "crop": "lettuce",
                "quantity_kg": 140,
                "volume_m3": 1.6,
                "packing": "crate",
                "fragile": True,
                "requires_refrigeration": True,
                "max_stack_weight_kg": 30,
            },
            "harvest_ready_date": "2026-06-12T06:30:00Z",
            "perishability_hours": 72,
            "pickup_window": {"earliest": "06:00", "latest": "10:00"},
            "road_segment": "R12",
            "status": "ready",
            "storm_impact": {"affected": False, "yield_change_pct": 0, "revised_from_quantity_kg": None},
            "updated_at": NOW,
        },
        {
            "_id": "farm_D_batch_0612",
            "farm_id": "farm_D",
            "farm_name": "Sunny Acre Tomatoes",
            "location": {"lat": 52.160, "lng": 0.210, "address": "Sunny Acre"},
            "stock": {
                "crop": "tomatoes",
                "quantity_kg": 260,
                "volume_m3": 1.4,
                "packing": "tray",
                "fragile": True,
                "requires_refrigeration": False,
                "max_stack_weight_kg": 15,
            },
            "harvest_ready_date": "2026-06-12T07:00:00Z",
            "perishability_hours": 120,
            "pickup_window": {"earliest": "07:00", "latest": "13:00"},
            "road_segment": "R9",
            "status": "ready",
            "storm_impact": {"affected": False, "yield_change_pct": 0, "revised_from_quantity_kg": None},
            "updated_at": NOW,
        },
        {
            "_id": "farm_E_batch_0612",
            "farm_id": "farm_E",
            "farm_name": "Long Field Produce",
            "location": {"lat": 52.140, "lng": 0.060, "address": "Long Field, West Rd"},
            "stock": {
                "crop": "carrots",
                "quantity_kg": 430,
                "volume_m3": 0.8,
                "packing": "sack",
                "fragile": False,
                "requires_refrigeration": False,
                "max_stack_weight_kg": 250,
            },
            "harvest_ready_date": "2026-06-12T05:30:00Z",
            "perishability_hours": 480,
            "pickup_window": {"earliest": "05:30", "latest": "15:00"},
            "road_segment": "R7",
            "status": "ready",
            "storm_impact": {"affected": False, "yield_change_pct": 0, "revised_from_quantity_kg": None},
            "updated_at": NOW,
        },
    ]


def baseline_roads():
    return [
        {"_id": "R7", "segment": "R7", "passable": True, "delay_min": 0, "updated_at": NOW},
        {"_id": "R9", "segment": "R9", "passable": True, "delay_min": 0, "updated_at": NOW},
        {"_id": "R12", "segment": "R12", "passable": True, "delay_min": 0, "updated_at": NOW},
    ]


# --- STORM: derive the "after" state from baseline -------------------------
# What the Farmer Agent would write once a storm hits. The Transport Agent
# reads these revised docs and re-plans — this is the headline demo moment.
def apply_storm(farms, roads):
    for f in farms:
        # farm_A: greenhouse partly protected but 30% loss on fragile strawberries
        if f["farm_id"] == "farm_A":
            orig = f["stock"]["quantity_kg"]
            f["stock"]["quantity_kg"] = round(orig * 0.70)
            f["storm_impact"] = {"affected": True, "yield_change_pct": -30, "revised_from_quantity_kg": orig}
        # farm_B: harvest delayed a day (won't make this run's pickup window)
        if f["farm_id"] == "farm_B":
            f["harvest_ready_date"] = "2026-06-13T05:00:00Z"
            f["status"] = "delayed"
            f["storm_impact"] = {"affected": True, "yield_change_pct": 0, "revised_from_quantity_kg": None}
        # farm_E: yield too low to justify a stop after the storm
        if f["farm_id"] == "farm_E":
            orig = f["stock"]["quantity_kg"]
            f["stock"]["quantity_kg"] = round(orig * 0.20)
            f["storm_impact"] = {"affected": True, "yield_change_pct": -80, "revised_from_quantity_kg": orig}
        f["updated_at"] = NOW

    # Road R12 flooded — blocks the direct route to farm_A and farm_C.
    for r in roads:
        if r["segment"] == "R12":
            r["passable"] = False
            r["delay_min"] = 999
            r["updated_at"] = NOW
    return farms, roads


def connect():
    if not CONN:
        sys.exit("Set MDB_MCP_CONNECTION_STRING (or MONGODB_URI) to your Atlas connection string first.")
    db = MongoClient(CONN)[DB_NAME]
    db.command("ping")  # fail fast if the connection string is wrong
    return db


def seed(db, storm=False, wipe=False):
    farms, roads = baseline_farms(), baseline_roads()
    if storm:
        farms, roads = apply_storm(farms, roads)

    if wipe:
        db.farms.delete_many({})
        db.road_conditions.delete_many({})
        db.transport_plans.delete_many({})  # clear stale plans so re-plan tests start clean

    # Upsert so re-running is idempotent (re-seeding never duplicates).
    for f in farms:
        db.farms.replace_one({"_id": f["_id"]}, f, upsert=True)
    for r in roads:
        db.road_conditions.replace_one({"_id": r["_id"]}, r, upsert=True)

    label = "STORM" if storm else "BASELINE"
    print(f"Seeded {label}: {len(farms)} farms, {len(roads)} road segments into '{DB_NAME}'.")
    show(db)


def show(db):
    print("\n  farm   crop          qty_kg  ref?  fragile  status   road  storm")
    print("  " + "-" * 64)
    for f in db.farms.find().sort("_id", 1):
        s, si = f["stock"], f["storm_impact"]
        flag = f"{si['yield_change_pct']:+d}%" if si["affected"] else "-"
        print(f"  {f['farm_id']:5}  {s['crop']:12}  {s['quantity_kg']:6}  "
              f"{'Y' if s['requires_refrigeration'] else 'n':4}  "
              f"{'Y' if s['fragile'] else 'n':7}  {f['status']:7}  {f['road_segment']:4}  {flag}")
    blocked = [r["segment"] for r in db.road_conditions.find({"passable": False})]
    print(f"\n  impassable roads: {blocked or 'none'}\n")


def main():
    p = argparse.ArgumentParser(description="Seed the shared agri cluster.")
    p.add_argument("--storm", action="store_true", help="seed the storm scenario")
    p.add_argument("--reset", action="store_true", help="wipe collections, then seed baseline")
    p.add_argument("--show", action="store_true", help="print current farms only")
    args = p.parse_args()

    db = connect()
    if args.show:
        show(db)
    elif args.reset:
        seed(db, storm=False, wipe=True)
    else:
        seed(db, storm=args.storm, wipe=False)


if __name__ == "__main__":
    main()
