"""Transport Agent config — same env-loading pattern as greenhouse/backend/config.py."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# SAME Atlas cluster as the greenhouse — this is how the agents share the blackboard.
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB = os.getenv("MONGODB_DB", "agent_greenhouse")

GCP_PROJECT = os.getenv("GCP_PROJECT", "centered-radio-497405-u9")
GCP_REGION = os.getenv("GCP_REGION", "us-central1")

# URL of the OR-Tools route-optimizer service (Cloud Run). Empty => greedy fallback.
OPTIMIZER_URL = os.getenv("OPTIMIZER_URL", "")
