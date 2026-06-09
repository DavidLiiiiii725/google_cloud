from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
from app.database import db
from app.routes import vendors, analytics, group_orders
from app.routes import merchant

TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def _ensure_indexes():
        await db.vendors.create_index([("location", "2dsphere")])
        await db.group_orders.create_index([("location", "2dsphere")])
    try:
        await asyncio.wait_for(_ensure_indexes(), timeout=5.0)
        print("VendorGroove Net online. Geo-indexes ready.")
    except Exception as e:
        print(f"VendorGroove Net: MongoDB unavailable ({type(e).__name__}). Running in analytics-only mode.")
    yield
    print("VendorGroove Net shutting down.")


app = FastAPI(title="VendorGroove Net", version="1.0.0", lifespan=lifespan)

app.include_router(merchant.router)

_static_dir = Path(__file__).parent / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

app.include_router(vendors.router,      prefix="/api/vendors",      tags=["vendors"])
app.include_router(analytics.router,    prefix="/api/analytics",    tags=["analytics"])
app.include_router(group_orders.router, prefix="/api/group-orders", tags=["group-orders"])


@app.get("/", response_class=HTMLResponse)
async def landing():
    return (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return (TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "VendorGroove Net"}
