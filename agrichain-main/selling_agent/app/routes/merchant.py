from fastapi import APIRouter, Body
from app.database import agrichain_db

router = APIRouter()

@router.post("/merchant/allocate")
async def allocate(version: int = Body(..., embed=True)):
    # read the transport plan the orchestrator just produced
    plan = await agrichain_db.transport_plans.find_one({"plan_version": version})
    if not plan:
        return {"error": "no plan found"}

    # total supply actually arriving
    served_kg = sum(s["pickup_kg"] for v in plan["vehicles"] for s in v["route"])

    # shortfall-aware pricing: less supply -> higher price
    base_price = 2.0
    unit_price = round(base_price * (1 + max(0, (1200 - served_kg)) / 3000), 2)

    order_doc = {
        "_id": f"orders_{version}",
        "from_plan": plan["_id"],
        "total_supply_kg": served_kg,
        "unit_price": unit_price,
        "note": "scarcity pricing applied" if unit_price > base_price else "normal pricing",
    }
    await agrichain_db.orders.replace_one({"_id": order_doc["_id"]}, order_doc, upsert=True)
    return order_doc