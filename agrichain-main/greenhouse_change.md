# Greenhouse change — the ONE addition to make it a supply-chain participant

The greenhouse agent currently writes `live_telemetry` and `incidents`. To join the
blackboard it needs exactly one new behavior: **publish its farm's harvestable state to
the `farms` collection**, and **lower the yield when a storm is active**. Nothing else
about the greenhouse changes.

## 1. Add a `farm_publish.py` to `greenhouse/backend/`

```python
"""Publish this greenhouse's harvestable state to the shared `farms` collection.
Called after an incident resolves (yield known) and whenever a storm world_event flips."""
from __future__ import annotations
import time
from . import db, state

FARM_ID = "farm-eldoret-01"          # this greenhouse's identity in the supply chain
SCENARIO = "default"

# Base harvestable stock when healthy. zone_health scales quantity down under stress.
BASE_STOCK = [
    {"sku": "tomato-loose", "crop": "tomato", "base_kg": 320, "volume_l": 480,
     "packing": "loose", "fragile": True, "stackable": False,
     "perishable": True, "refrigerated": False},
    {"sku": "avocado-crate", "crop": "avocado", "base_kg": 540, "volume_l": 600,
     "packing": "crate", "fragile": False, "stackable": True,
     "perishable": True, "refrigerated": True},
]


def publish_farm():
    """Upsert this farm's doc. Quantity scales with harvest-zone health (storm => lower)."""
    zh = state.get_zone_health()
    health = zh.get("harvest", 1.0)          # harvest zone drives sellable yield
    storm = db.world_events().find_one(
        {"scenario_id": SCENARIO, "type": "storm", "status": "active"})
    mult = health * (storm["effects"].get("yield_multiplier", 1.0) if storm else 1.0)

    now = time.time()
    stock = [{
        "sku": s["sku"], "crop": s["crop"],
        "quantity_kg": round(s["base_kg"] * mult, 1),
        "volume_l": s["volume_l"], "packing": s["packing"],
        "fragile": s["fragile"], "stackable": s["stackable"],
        "perishable": s["perishable"], "refrigerated": s["refrigerated"],
        "ready_at": now, "deadline_at": now + 36000,
    } for s in BASE_STOCK if s["base_kg"] * mult >= 1.0]

    yield_status = "normal" if mult > 0.85 else "reduced" if mult > 0.1 else "destroyed"

    db.farms().update_one(
        {"_id": FARM_ID},
        {"$set": {
            "_id": FARM_ID, "scenario_id": SCENARIO, "name": "Eldoret North Co-op",
            "location": {"lat": 0.5143, "lng": 35.2698, "label": "Eldoret North"},
            "access": "open", "yield_status": yield_status, "stock": stock,
            "updated_at": now, "updated_by": "farmer",
        }},
        upsert=True,
    )
```

## 2. Add `farms()` and `world_events()` accessors to `greenhouse/backend/db.py`

Same two-line additions as in `transport/backend/db.py`:

```python
def farms():
    return get_db()["farms"]

def world_events():
    return get_db()["world_events"]
```

## 3. Call `publish_farm()` in two places in `greenhouse/backend/pipeline.py`

- At the end of `run_cycle`, in the `if sev == "nominal":` branch (after resolving an
  incident) — so yields are published once conditions settle.
- Inside `execute_plan`, after the actuators are applied — so a storm-driven drop is
  reflected promptly.

```python
from . import farm_publish
# ... after the relevant state update:
farm_publish.publish_farm()
```

That's the whole greenhouse-side merge. The greenhouse keeps running exactly as it does
now; it just also keeps its `farms` doc current, which is what your Transport Agent reads.
