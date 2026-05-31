"""Demo farm data for the Transport Agent.

Five smallholder farms with deliberately varied stock so the agent's compatibility
reasoning has real work to do:
  - refrigerated vs ambient (cold chain must stay separate)
  - loose/fragile vs crated/stackable (fragile can't go under heavy crates)
  - perishable (tight deadlines) vs durable (loose deadlines)
This variety is what makes the consolidated plan — and the storm re-plan — visibly
non-trivial. seed_farms() resets the blackboard; fire_storm() applies the disruption
the way the Farmer Agent would (lower yields, block a road).
"""
from __future__ import annotations
import time
from . import db

SCENARIO = "default"


def seed_farms():
    """Reset the blackboard and plant five demo farms. Idempotent."""
    db.farms().delete_many({"scenario_id": SCENARIO})
    db.world_events().delete_many({"scenario_id": SCENARIO})
    db.transport_plans().delete_many({"scenario_id": SCENARIO})
    now = time.time()
    H = 3600

    farms = [
        {
            "_id": "farm-eldoret-01", "name": "Eldoret North Co-op",
            "location": {"lat": 0.5143, "lng": 35.2698, "label": "Eldoret North"},
            "stock": [
                {"sku": "tomato-loose", "crop": "tomato", "quantity_kg": 320, "volume_l": 480,
                 "packing": "loose", "fragile": True, "stackable": False,
                 "perishable": True, "refrigerated": False,
                 "ready_at": now, "deadline_at": now + 10 * H},
                {"sku": "avocado-crate", "crop": "avocado", "quantity_kg": 540, "volume_l": 600,
                 "packing": "crate", "fragile": False, "stackable": True,
                 "perishable": True, "refrigerated": True,
                 "ready_at": now, "deadline_at": now + 20 * H},
            ],
        },
        {
            "_id": "farm-kapsabet-03", "name": "Kapsabet South",
            "location": {"lat": 0.2030, "lng": 35.1050, "label": "Kapsabet"},
            "stock": [
                {"sku": "maize-sack", "crop": "maize", "quantity_kg": 800, "volume_l": 900,
                 "packing": "sack", "fragile": False, "stackable": True,
                 "perishable": False, "refrigerated": False,
                 "ready_at": now, "deadline_at": now + 60 * H},
            ],
        },
        {
            "_id": "farm-iten-02", "name": "Iten Highland Growers",
            "location": {"lat": 0.6700, "lng": 35.5080, "label": "Iten"},
            "stock": [
                {"sku": "strawberry-punnet", "crop": "strawberry", "quantity_kg": 140, "volume_l": 260,
                 "packing": "carton", "fragile": True, "stackable": False,
                 "perishable": True, "refrigerated": True,
                 "ready_at": now, "deadline_at": now + 8 * H},
                {"sku": "potato-sack", "crop": "potato", "quantity_kg": 620, "volume_l": 700,
                 "packing": "sack", "fragile": False, "stackable": True,
                 "perishable": False, "refrigerated": False,
                 "ready_at": now, "deadline_at": now + 72 * H},
            ],
        },
        {
            "_id": "farm-kabarnet-05", "name": "Kabarnet Valley Farm",
            "location": {"lat": 0.4900, "lng": 35.7430, "label": "Kabarnet"},
            "stock": [
                {"sku": "mango-crate", "crop": "mango", "quantity_kg": 410, "volume_l": 520,
                 "packing": "crate", "fragile": False, "stackable": True,
                 "perishable": True, "refrigerated": False,
                 "ready_at": now, "deadline_at": now + 16 * H},
            ],
        },
        {
            "_id": "farm-nandi-04", "name": "Nandi Hills Dairy & Produce",
            "location": {"lat": 0.1010, "lng": 35.1780, "label": "Nandi Hills"},
            "stock": [
                {"sku": "milk-chilled", "crop": "milk", "quantity_kg": 480, "volume_l": 480,
                 "packing": "pallet", "fragile": False, "stackable": True,
                 "perishable": True, "refrigerated": True,
                 "ready_at": now, "deadline_at": now + 6 * H},
                {"sku": "kale-loose", "crop": "kale", "quantity_kg": 90, "volume_l": 210,
                 "packing": "loose", "fragile": True, "stackable": False,
                 "perishable": True, "refrigerated": False,
                 "ready_at": now, "deadline_at": now + 9 * H},
            ],
        },
    ]

    for f in farms:
        db.farms().insert_one({
            **f, "scenario_id": SCENARIO, "access": "open", "yield_status": "normal",
            "updated_at": now, "updated_by": "farmer",
        })
    return len(farms)


def fire_storm():
    """Apply the storm the way the Farmer Agent + orchestrator would:
    write an active world_event, block one road, reduce two yields, wipe one farm.
    Returns the event id. (Re-plan is triggered separately via the /replan endpoint.)
    """
    if db.farms().count_documents({"scenario_id": SCENARIO}) == 0:
        seed_farms()
    now = time.time()
    evt_id = f"evt-storm-{int(now)}"
    db.world_events().insert_one({
        "_id": evt_id, "scenario_id": SCENARIO, "type": "storm", "status": "active",
        "severity": "critical",
        "effects": {"yield_multiplier": 0.6,
                    "roads_blocked": ["farm-kapsabet-03", "farm-nandi-04"]},
        "created_at": now, "updated_at": now, "updated_by": "orchestrator",
    })
    # Farmer-agent reactions to the storm:
    db.farms().update_one({"_id": "farm-kapsabet-03"},
                          {"$set": {"access": "blocked", "yield_status": "destroyed"}})
    db.farms().update_one({"_id": "farm-nandi-04"},
                          {"$set": {"access": "blocked", "yield_status": "destroyed"}})
    db.farms().update_one({"_id": "farm-iten-02"},
                          {"$set": {"yield_status": "reduced"}})
    return evt_id
