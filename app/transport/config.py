"""Transport Agent config. ROOT is the project root so we share .env with the greenhouse."""
import os
from pathlib import Path
from dotenv import load_dotenv

# app/transport/config.py → app/transport → app → project root
ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

# SAME Atlas cluster as the greenhouse — this is how the agents share the blackboard.
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB = os.getenv("MONGODB_DB", "agent_greenhouse")

GCP_PROJECT = os.getenv("GCP_PROJECT", "centered-radio-497405-u9")
GCP_REGION = os.getenv("GCP_REGION", "us-central1")

# URL of the OR-Tools route-optimizer service (Cloud Run or local).
# Empty => greedy fallback inside the agent (still works, just not optimal).
OPTIMIZER_URL = os.getenv("OPTIMIZER_URL", "http://localhost:8080")

SCENARIO = os.getenv("SCENARIO", "default")
