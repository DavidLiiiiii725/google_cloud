"""Greenhouse config. ROOT is the project root so we share .env + assets/ with transport."""
import os
from pathlib import Path
from dotenv import load_dotenv

# app/greenhouse/config.py → app/greenhouse → app → project root
ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

# Shared with the transport agent — same Atlas cluster, same DB name, so both
# services see the same farms / world_events / transport_plans / agent_logs docs.
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB = os.getenv("MONGODB_DB", "agent_greenhouse")

GCP_PROJECT = os.getenv("GCP_PROJECT", "centered-radio-497405-u9")
GCP_REGION = os.getenv("GCP_REGION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-001")

GCS_BUCKET = os.getenv("GCS_BUCKET", "")

ASSETS_DIR = ROOT / "assets"
WEB_DIR = ROOT / "web"
ZONES = ["seedling", "growing", "harvest"]
ZONE_SENSITIVITY = [0.7, 1.0, 1.3]  # harvest wilts fastest; seedling most resilient

# This greenhouse's identity in the supply-chain blackboard.
FARM_ID = os.getenv("GREENHOUSE_FARM_ID", "farm-eldoret-01")
SCENARIO = os.getenv("SCENARIO", "default")
