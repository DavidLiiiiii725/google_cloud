from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class GeoLocation(BaseModel):
    type: str = "Point"
    coordinates: List[float]  # [longitude, latitude]


class VendorCreate(BaseModel):
    shop_name: str
    location: GeoLocation


class Vendor(VendorCreate):
    id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True
