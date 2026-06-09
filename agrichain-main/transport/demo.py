"""Seed two farms and run the full storm cascade against the in-memory store.

Run this from INSIDE the transport service process space — i.e. it imports the same
backend modules the server uses, so the farms it seeds are visible to /plan and /replan.

Usage (from the transport/ directory, with the venv active):
    python demo.py
"""
import asyncio
import json
import time
from backend import db, pipeline


def seed_farms():
    db.farms().delete_many({})
    db.world_events().delete_many({})
    db.transport_plans().delete_many({})
    now = time.time()
    db.farms().insert_one({
        "_id": "farm-eldoret-01", "scenario_id": "default", "name": "Eldoret North Co-op",
        "location": {"lat": 0.5143, "lng": 35.2698, "label": "Eldoret North"},
        "access": "open", "yield_status": "normal",
        "stock": [
            {"sku": "tomato-loose", "crop": "tomato", "quantity_kg": 320, "volume_l": 480,
             "packing": "loose", "fragile": True, "stackable": False,
             "perishable": True, "refrigerated": False,
             "ready_at": now, "deadline_at": now + 10 * 3600},
            {"sku": "avocado-crate", "crop": "avocado", "quantity_kg": 540, "volume_l": 600,
             "packing": "crate", "fragile": False, "stackable": True,
             "perishable": True, "refrigerated": True,
             "ready_at": now, "deadline_at": now + 20 * 3600},
        ],
        "updated_at": now, "updated_by": "farmer",
    })
    db.farms().insert_one({
        "_id": "farm-kapsabet-03", "scenario_id": "default", "name": "Kapsabet South",
        "location": {"lat": 0.20, "lng": 35.10, "label": "Kapsabet"},
        "access": "open", "yield_status": "normal",
        "stock": [
            {"sku": "maize-sack", "crop": "maize", "quantity_kg": 800, "volume_l": 900,
             "packing": "sack", "fragile": False, "stackable": True,
             "perishable": False, "refrigerated": False,
             "ready_at": now, "deadline_at": now + 60 * 3600},
        ],
        "updated_at": now, "updated_by": "farmer",
    })
    print("Seeded 2 farms.\n")


def storm():
    now = time.time()
    db.world_events().insert_one({
        "_id": "evt-storm-01", "scenario_id": "default", "type": "storm", "status": "active",
        "severity": "critical",
        "effects": {"yield_multiplier": 0.6, "roads_blocked": ["farm-kapsabet-03"]},
        "created_at": now, "updated_at": now, "updated_by": "orchestrator",
    })
    # Simulate the Farmer Agent reacting to the storm:
    db.farms().update_one({"_id": "farm-kapsabet-03"},
                          {"$set": {"access": "blocked", "yield_status": "destroyed"}})
    db.farms().update_one({"_id": "farm-eldoret-01"},
                          {"$set": {"yield_status": "reduced"}})
    print("\nStorm fired: farm-kapsabet-03 road blocked, eldoret yield reduced.\n")


async def main():
    seed_farms()
    print("=== INITIAL PLAN ===")
    p1 = await pipeline.build_plan(trigger="initial")
    print(f"  {len(p1['vehicles'])} vehicles, cost {p1['cost']['total_cents']}c\n")

    storm()

    print("=== STORM RE-PLAN ===")
    p2 = await pipeline.replan()
    print(f"  {len(p2['vehicles'])} vehicles, cost {p2['cost']['total_cents']}c, "
          f"{len(p2['unrouted'])} unrouted\n")
    print("=== DIFF ===")
    print(json.dumps(p2["reasoning"]["replan_diff"], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
