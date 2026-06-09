from fastapi import APIRouter, HTTPException
from app.database import db
from app.models.vendor import VendorCreate
from datetime import datetime

router = APIRouter()


@router.post("/register")
async def register_vendor(vendor: VendorCreate):
    doc = {
        "shop_name": vendor.shop_name,
        "location": vendor.location.dict(),
        "created_at": datetime.utcnow(),
        "is_active": True,
    }
    result = await db.vendors.insert_one(doc)
    return {"id": str(result.inserted_id), "message": "Vendor registered successfully"}


@router.get("/nearby")
async def get_nearby_vendors(lat: float, lng: float, radius: int = 5000):
    try:
        cursor = db.vendors.find({
            "location": {
                "$near": {
                    "$geometry": {"type": "Point", "coordinates": [lng, lat]},
                    "$maxDistance": radius,
                }
            }
        }).limit(20)
        vendors = await cursor.to_list(20)
        for v in vendors:
            v["_id"] = str(v["_id"])
        return vendors
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/all")
async def get_all_vendors():
    cursor = db.vendors.find({}).limit(50)
    vendors = await cursor.to_list(50)
    for v in vendors:
        v["_id"] = str(v["_id"])
    return vendors
