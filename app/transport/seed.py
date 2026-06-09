"""Demo farm data for the Transport Agent.

Four peer farms — the fifth (farm-eldoret-01) is owned by the live Greenhouse Agent,
which publishes its own doc via app/greenhouse/farm_publish.py. Stock is deliberately
varied so the agent's compatibility reasoning has real work to do:
  - refrigerated vs ambient (cold chain must stay separate)
  - loose/fragile vs crated/stackable (fragile can't go under heavy crates)
  - perishable (tight deadlines) vs durable (loose deadlines)
This variety is what makes the consolidated plan — and the storm re-plan — visibly
non-trivial. seed_farms() resets only the peer farms; fire_storm() applies the
disruption the way the Farmer Agent would (lower yields, block a road).
"""
from __future__ import annotations
import time
from . import db, config

SCENARIO = config.SCENARIO

# Farms in this set are owned by an external agent (the live Greenhouse) and must NOT
# be wiped on reseed — that agent maintains its own farm doc on the blackboard.
EXTERNAL_FARM_IDS = {"farm-eldoret-01"}


def seed_farms():
    """Reset peer farms and clear prior plans/events. Externally-owned farms are preserved."""
    # Delete only farms we own. Farms in EXTERNAL_FARM_IDS belong to other agents
    # (the live Greenhouse) and must stay; everything else gets a clean slate.
    for f in list(db.farms().find({"scenario_id": SCENARIO})):
        if f["_id"] in EXTERNAL_FARM_IDS:
            continue
        db.farms().delete_many({"_id": f["_id"]})
    db.world_events().delete_many({"scenario_id": SCENARIO})
    db.transport_plans().delete_many({"scenario_id": SCENARIO})
    now = time.time()
    H = 3600

    # Peer farms — the Greenhouse Agent supplies farm-eldoret-01 separately.
    farms = [
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
        # Upsert (not insert) so reseed during a live session replaces cleanly even if
        # the prior delete was skipped (e.g., flag mismatch).
        db.farms().update_one({"_id": f["_id"]}, {"$set": {
            **f, "scenario_id": SCENARIO, "access": "open", "yield_status": "normal",
            "updated_at": now, "updated_by": "transport-seed",
        }}, upsert=True)
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
