"""
Dynamic Group Buying — Cold-Chain Radius Match
GeoJSON $near query on MongoDB group_orders collection (2dsphere index).
"""
import random
import math
from datetime import datetime, timedelta
from fastapi import APIRouter
from pydantic import BaseModel
from app.database import db

router = APIRouter()

TRUCK_IDS = ["A9", "B3", "C7", "D2", "E5", "F8", "G1", "H4", "J6", "K2"]

# Mock vendor pool — offset in degrees from user's position
MOCK_VENDOR_POOL = [
    {"name": "Green Leaf Market",     "dlng":  0.0031, "dlat":  0.0022},
    {"name": "Sunny Fresh Produce",   "dlng":  0.0014, "dlat": -0.0031},
    {"name": "Valley Garden Stand",   "dlng": -0.0041, "dlat":  0.0012},
    {"name": "Farm Direct Corner",    "dlng": -0.0027, "dlat": -0.0019},
    {"name": "Harvest Hub Express",   "dlng":  0.0052, "dlat":  0.0008},
]


class GroupOrderRequest(BaseModel):
    vendor_name:      str
    produce_type:     str
    quantity:         float
    unit:             str          # "lbs" | "bags" | "boxes" | "crates"
    delivery_window:  str          # "06:00 AM – 09:00 AM"
    location:         dict         # GeoJSON Point


def _haversine_mi(lat1, lng1, lat2, lng2) -> float:
    """Approximate distance in miles between two lat/lng pairs."""
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))


@router.post("/submit")
async def submit_group_order(order: GroupOrderRequest):
    """
    1. Write order to MongoDB as GeoJSON Point.
    2. Run $near query to find vendors within 5 miles in the same time window.
    3. Supplement with mock vendors to always guarantee a match (demo).
    4. Assign a Cold-Chain Truck ID.
    """
    lng = order.location["coordinates"][0]
    lat = order.location["coordinates"][1]

    # ── Step 1: Persist to MongoDB ──────────────────────────────────
    doc = {
        "vendor_name":     order.vendor_name,
        "produce_type":    order.produce_type,
        "quantity":        order.quantity,
        "unit":            order.unit,
        "delivery_window": order.delivery_window,
        "location":        order.location,          # GeoJSON Point
        "status":          "pending",
        "created_at":      datetime.utcnow(),
        "expires_at":      datetime.utcnow() + timedelta(hours=6),
    }

    order_id   = None
    real_nearby = []

    try:
        result   = await db.group_orders.insert_one(doc)
        order_id = str(result.inserted_id)

        # ── Step 2: $near geospatial query ──────────────────────────
        # Filter: same delivery window + within 8 047 m (~5 miles) + pending
        cursor = db.group_orders.find({
            "_id":             {"$ne": result.inserted_id},
            "delivery_window": order.delivery_window,
            "status":          "pending",
            "location": {
                "$near": {
                    "$geometry": {
                        "type":        "Point",
                        "coordinates": [lng, lat],
                    },
                    "$maxDistance": 8047,   # metres ≈ 5 miles
                }
            },
        }).limit(5)
        real_nearby = await cursor.to_list(5)

        # Mark order as matched
        await db.group_orders.update_one(
            {"_id": result.inserted_id},
            {"$set": {"status": "matched", "matched_at": datetime.utcnow()}},
        )

    except Exception as exc:
        print(f"[group_orders] MongoDB error — using mock fallback: {exc}")

    # ── Step 3: Build matched-vendor list ───────────────────────────
    matched: list[dict] = []

    for v in real_nearby[:2]:
        vlng, vlat = v["location"]["coordinates"]
        matched.append({
            "vendor_name":  v["vendor_name"],
            "lat":          vlat,
            "lng":          vlng,
            "distance_mi":  round(_haversine_mi(lat, lng, vlat, vlng), 2),
        })

    # Supplement with mock vendors until we have at least 2 others
    used_names = {m["vendor_name"] for m in matched}
    pool = [v for v in MOCK_VENDOR_POOL if v["name"] not in used_names]
    random.shuffle(pool)

    for mock in pool[:max(0, 2 - len(matched))]:
        mlat = lat + mock["dlat"]
        mlng = lng + mock["dlng"]
        matched.append({
            "vendor_name":  mock["name"],
            "lat":          round(mlat, 6),
            "lng":          round(mlng, 6),
            "distance_mi":  round(_haversine_mi(lat, lng, mlat, mlng), 2),
        })

    truck_id = random.choice(TRUCK_IDS)

    return {
        "success":         True,
        "order_id":        order_id or f"MOCK-{random.randint(100000,999999)}",
        "truck_id":        truck_id,
        "your_order": {
            "produce_type":    order.produce_type,
            "quantity":        order.quantity,
            "unit":            order.unit,
            "delivery_window": order.delivery_window,
        },
        "matched_vendors":     matched[:2],
        "total_in_truck":      len(matched) + 1,
        "service_radius_mi":   5,
        "pickup_eta":          "Tomorrow 5:30 AM",
        "algorithm":           "$near · maxDistance=8047m · same delivery_window",
    }


@router.get("/active")
async def get_active_orders():
    """Return all pending group orders (for map overlay)."""
    try:
        cursor = db.group_orders.find({"status": "pending"}).limit(50)
        orders = await cursor.to_list(50)
        for o in orders:
            o["_id"]        = str(o["_id"])
            o["created_at"] = o["created_at"].isoformat()
        return orders
    except Exception:
        return []
