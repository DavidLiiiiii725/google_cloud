"""Publish this greenhouse's harvestable state to the shared `farms` collection.

This is the bridge between the greenhouse and the supply-chain blackboard:
  • zone_health drives sellable yield (harvest zone is the main driver)
  • an active storm in world_events further multiplies the yield down
  • when a road is blocked by the storm, access flips to "blocked"

The Transport Agent reads these docs and re-plans when they change.
"""
from __future__ import annotations
import time
from . import db, state, config, events

# Base harvestable stock when the greenhouse is fully healthy and no storm is active.
# zone_health (and a storm yield multiplier) scale quantity down. The schema fields
# match shared/schemas/farms.schema.json so the Transport Agent's compatibility rules
# (refrigerated vs ambient, fragile vs stackable) make sense.
BASE_STOCK = [
    {"sku": "tomato-loose", "crop": "tomato", "base_kg": 320, "volume_l": 480,
     "packing": "loose", "fragile": True, "stackable": False,
     "perishable": True, "refrigerated": False},
    {"sku": "avocado-crate", "crop": "avocado", "base_kg": 540, "volume_l": 600,
     "packing": "crate", "fragile": False, "stackable": True,
     "perishable": True, "refrigerated": True},
]

# Internal: last published multiplier, used to suppress duplicate writes when nothing
# meaningful has changed (avoids storming the blackboard on every 2s heartbeat).
_last_mult: float | None = None
_last_access: str | None = None


def _active_storm() -> dict | None:
    return db.world_events().find_one(
        {"scenario_id": config.SCENARIO, "type": "storm", "status": "active"})


def publish_farm(force: bool = False) -> dict | None:
    """Upsert this farm's doc. Returns the document written, or None if skipped.

    Quantity scales with harvest-zone health × storm multiplier.
    Access flips to "blocked" if the storm names this farm in roads_blocked.
    """
    global _last_mult, _last_access
    zh = state.get_zone_health()
    health = zh.get("harvest", 1.0)  # harvest zone drives sellable yield

    storm = _active_storm()
    storm_mult = 1.0
    blocked = False
    if storm:
        storm_mult = float(storm.get("effects", {}).get("yield_multiplier", 1.0))
        roads = storm.get("effects", {}).get("roads_blocked", []) or []
        blocked = config.FARM_ID in roads

    mult = max(0.0, min(1.0, health * storm_mult))

    # Skip if nothing meaningful changed (multiplier within 0.02 and access same).
    access = "blocked" if blocked else "open"
    if not force and _last_mult is not None and access == _last_access:
        if abs(mult - _last_mult) < 0.02:
            return None

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

    doc = {
        "_id": config.FARM_ID,
        "scenario_id": config.SCENARIO,
        "name": "Eldoret North Co-op",
        "location": {"lat": 0.5143, "lng": 35.2698, "label": "Eldoret North"},
        "access": access,
        "yield_status": yield_status,
        "stock": stock,
        "updated_at": now,
        "updated_by": "farmer",
    }
    db.farms().update_one({"_id": config.FARM_ID}, {"$set": doc}, upsert=True)
    _last_mult = mult
    _last_access = access
    events.emit("INFO", f"Farm published — {config.FARM_ID} "
                        f"yield={yield_status} ({round(mult*100)}%) access={access}")
    return doc
