import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB = os.getenv("MONGODB_DB", "agent_greenhouse")

GCP_PROJECT = os.getenv("GCP_PROJECT", "centered-radio-497405-u9")
GCP_REGION = os.getenv("GCP_REGION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-001")

GCS_BUCKET = os.getenv("GCS_BUCKET", "")

ASSETS_DIR = ROOT / "assets"
ZONES = ["seedling", "growing", "harvest"]
ZONE_SENSITIVITY = [0.7, 1.0, 1.3]  # harvest wilts fastest; seedling most resilient
