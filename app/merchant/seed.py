"""Demo buyer demand-book for the Merchant Agent.

Four buyers with competing standing orders across the crops the farms produce
(tomato, avocado, maize, strawberry, potato, mango, milk, kale). Priority encodes who
gets served first when supply is short: 1 = essential (hospital), 4 = discretionary
(premium exporter). Under a storm the arriving supply drops below total demand, so the
Merchant's priority fair-share allocation and scarcity pricing have real work to do —
that visible reallocation is the third agent's contribution to the cascade.
"""
from __future__ import annotations
import time
from . import db, config

SCENARIO = config.SCENARIO

# Standing orders. `priority`: lower = served first under scarcity.
BUYERS = [
    {
        "_id": "buyer-hospital-01", "name": "Eldoret Referral Hospital",
        "channel": "essential", "priority": 1,
        "location": {"lat": 0.5200, "lng": 35.2700, "label": "Eldoret"},
        "demand": [
            {"crop": "milk", "requested_kg": 220},
            {"crop": "tomato", "requested_kg": 120},
            {"crop": "kale", "requested_kg": 70},
            {"crop": "potato", "requested_kg": 150},
        ],
    },
    {
        "_id": "buyer-school-02", "name": "Moi University Dining",
        "channel": "institutional", "priority": 2,
        "location": {"lat": 0.2870, "lng": 35.2930, "label": "Kesses"},
        "demand": [
            {"crop": "maize", "requested_kg": 320},
            {"crop": "potato", "requested_kg": 260},
            {"crop": "tomato", "requested_kg": 110},
            {"crop": "mango", "requested_kg": 90},
        ],
    },
    {
        "_id": "buyer-market-03", "name": "Kapsabet Open Market",
        "channel": "retail", "priority": 3,
        "location": {"lat": 0.2030, "lng": 35.1050, "label": "Kapsabet"},
        "demand": [
            {"crop": "mango", "requested_kg": 220},
            {"crop": "avocado", "requested_kg": 240},
            {"crop": "strawberry", "requested_kg": 70},
            {"crop": "tomato", "requested_kg": 90},
        ],
    },
    {
        "_id": "buyer-export-04", "name": "FreshExport Co. (EU)",
        "channel": "export", "priority": 4,
        "location": {"lat": 0.5140, "lng": 35.2690, "label": "Eldoret Hub"},
        "demand": [
            {"crop": "avocado", "requested_kg": 320},
            {"crop": "strawberry", "requested_kg": 110},
            {"crop": "mango", "requested_kg": 160},
        ],
    },
]


def seed_buyers() -> int:
    """Upsert the buyer demand-book. Idempotent — safe to call on every startup."""
    now = time.time()
    for b in BUYERS:
        db.buyers().update_one({"_id": b["_id"]}, {"$set": {
            **b, "scenario_id": SCENARIO, "updated_at": now, "updated_by": "merchant-seed",
        }}, upsert=True)
    return len(BUYERS)


def reset_orders() -> int:
    """Clear prior market_orders for a clean demo run."""
    return db.market_orders().delete_many({"scenario_id": SCENARIO}).deleted_count
