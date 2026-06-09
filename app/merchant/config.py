"""Merchant Agent config. ROOT is the project root so all three agents share one .env.

The Merchant is the third agent on the blackboard. It reads the Transport Agent's
committed `transport_plans` and the live `farms`, decides how to allocate the supply
that actually arrives across competing buyers, and writes a `market_orders` doc. Same
MONGODB_URI / MONGODB_DB as the other two agents — that's how they share state.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# app/merchant/config.py → app/merchant → app → project root
ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

# SAME cluster + db as Greenhouse and Transport — the shared blackboard.
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB = os.getenv("MONGODB_DB", "agent_greenhouse")

GCP_PROJECT = os.getenv("GCP_PROJECT", "xl-icds-final")
GCP_REGION = os.getenv("GCP_REGION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

SCENARIO = os.getenv("SCENARIO", "default")
