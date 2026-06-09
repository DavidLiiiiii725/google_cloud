"""
Transport Agent (Google ADK) — the reasoning brain of the logistics layer.

Wires together two tools:
  1. MongoDB MCP server  -> reads `farms` / `road_conditions`, writes `transport_plans`
  2. optimize_routes()   -> calls the OR-Tools service for the actual routing math

The agent does the REASONING (which farms to include, how to turn packing /
refrigeration into optimizer inputs, sanity-checking the result, deciding when a
tiny pickup isn't worth a stop). The optimizer does the MATH. That division is
the answer to "why an agent and not just a solver?".

Run for local testing:
    adk web          # opens the ADK dev UI, pick "transport_agent"

Env vars needed (put in .env at the project root):
    MDB_MCP_CONNECTION_STRING = your working mongodb:// Atlas string
    OPTIMIZER_URL             = http://localhost:8080   (your route-optimizer service)
    GOOGLE_API_KEY            = your Gemini API key   (or Vertex AI config)
"""

import os
import requests

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

# Explicitly load the .env sitting next to this file, BEFORE building the
# toolset — otherwise the MongoDB MCP server starts unconfigured and the agent
# wastes dozens of turns guessing connection strings.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# Fail loudly and early if the connection string still isn't present, rather
# than letting the agent flail at runtime.
if not os.environ.get("MDB_MCP_CONNECTION_STRING"):
    raise RuntimeError(
        "MDB_MCP_CONNECTION_STRING is not set. Put it in transport_agent/.env "
        "(the working mongodb:// string) so the MongoDB MCP server can connect."
    )

OPTIMIZER_URL = os.environ.get("OPTIMIZER_URL", "http://localhost:8080")


# --- Custom tool: the bridge to the OR-Tools optimizer service --------------
def optimize_routes(
    depot: dict,
    vehicles: list[dict],
    stops: list[dict],
    cost_per_km: float = 1.5,
    drop_penalty: float = 100000.0,
) -> dict:
    """Compute the cost-optimal consolidation of farm pickups into vehicle routes.

    Call this AFTER you have decided which farms to include and translated each
    farm's stock into a stop. The optimizer handles weight capacity, volume
    capacity, the refrigerated/ambient split, vehicle fixed costs, and may drop
    a stop if serving it costs more than `drop_penalty`.

    Args:
        depot: {"lat": float, "lng": float} — where vehicles start and end.
        vehicles: list of {"id": str, "capacity_kg": float, "capacity_m3": float,
                  "refrigerated": bool, "fixed_cost": float}.
        stops: list of {"farm_id": str, "lat": float, "lng": float,
               "demand_kg": float, "demand_m3": float, "requires_refrigeration": bool}.
        cost_per_km: cost charged per km travelled.
        drop_penalty: cost of leaving a stop unserved. Lower it to let the
            optimizer skip tiny or far-flung pickups on purpose.

    Returns:
        A dict with the optimized plan: per-vehicle routes, unserved farms, and a
        summary (total_vehicles, total_distance_km, total_cost, served, dropped).
    """
    resp = requests.post(
        f"{OPTIMIZER_URL}/optimize",
        json={
            "depot": depot,
            "vehicles": vehicles,
            "stops": stops,
            "cost_per_km": cost_per_km,
            "drop_penalty": drop_penalty,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# --- Tool 1: MongoDB via the official MCP server ----------------------------
# Reuses the SAME connection string the seed script and VS Code setup use.
# tool_filter restricts the agent to ONLY the data tools it needs, so it can't
# wander into connect / logs / knowledge / atlas-admin tools and loop. The
# MDB_MCP_DISABLED_TOOLS env var also tells the server not to expose those
# categories at all (belt and suspenders).
mongodb_toolset = MCPToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="npx",
            args=["-y", "mongodb-mcp-server@latest"],
            env={
                "MDB_MCP_CONNECTION_STRING": os.environ["MDB_MCP_CONNECTION_STRING"],
                # Hide everything except core read/write CRUD tools.
                "MDB_MCP_DISABLED_TOOLS": "atlas,connect,logs,knowledge,export,session",
            },
        ),
        timeout=60,
    ),
    tool_filter=["find", "aggregate", "count", "insert-many", "update-many", "list-collections"],
)


# --- The agent: reasoning + two tools ---------------------------------------
INSTRUCTION = """
You are the Transport Agent in an agricultural supply chain. Your job is to
consolidate produce pickups from multiple small farms into cost-efficient
vehicle routes, and to RE-PLAN when a storm changes the situation.

You have two tools:
- MongoDB tools (from the MCP server): read the `farms` and `road_conditions`
  collections in the `agrichain` database; write your plan to `transport_plans`.
- optimize_routes: computes the actual cost-optimal routing once you give it a
  clean problem.

Your reasoning process for every planning request:

0. The database is ALREADY CONNECTED. Do not run diagnostics, do not check
   logs, do not search any knowledge base, do not list databases or deployments,
   and do not try to find or fix a connection. Your very first action is to query
   the `farms` collection in the `agrichain` database with the `find` tool. If a
   query returns data, the connection is fine — proceed. Never treat an empty or
   slow result as a connection problem.

1. READ STATE. Use `find` to read the `farms` and `road_conditions` collections
   in the `agrichain` database. Limit results sensibly.

2. DECIDE WHAT TO INCLUDE. Reason about each farm before sending it to the
   optimizer:
   - Skip farms whose status is not "ready" (e.g. "delayed") — they can't be
     picked up this run.
   - Skip a farm if its road_segment is currently impassable AND there is no
     sensible alternative — note it as unreachable.
   - Use judgment on tiny yields: if a farm's quantity is very small and it sits
     far from the others, it may not be worth a dedicated stop. You can either
     exclude it, or include it with a low drop_penalty so the optimizer decides.

3. TRANSLATE TO OPTIMIZER INPUTS. For each included farm, build a stop with its
   location, demand_kg (the current quantity), demand_m3, and
   requires_refrigeration (true for crops that need cold chain). Define a
   reasonable vehicle fleet (some refrigerated, some ambient).

4. OPTIMIZE. Call optimize_routes with the depot, vehicles, and stops.

5. SANITY-CHECK. Look at the result. Does it make sense? Are perishable
   (refrigerated) loads on refrigerated trucks? Is any tiny pickup causing a
   whole extra truck to be used? If something looks wrong, adjust your inputs
   (e.g. drop a marginal farm, change drop_penalty) and optimize again.

6. WRITE THE PLAN. Write a document to `transport_plans` with the vehicles and
   their routes, the unserved farms WITH REASONS, and a summary. If this is a
   re-plan triggered by a storm, set plan_version higher than the previous plan,
   set replanned_from to the previous plan's id, and write a short replan_reason
   explaining what changed (e.g. "farm_B delayed, road R12 impassable, dropped
   to 2 trucks").

Always explain your reasoning out loud as you go — which farms you included or
excluded and why, and what the optimizer returned. This visible reasoning is the
point: you are not a script, you are making judgment calls.
"""

root_agent = LlmAgent(
    model="gemini-3.5-flash",   # swap to the newest Gemini you have access to
    name="transport_agent",
    instruction=INSTRUCTION,
    tools=[
        mongodb_toolset,
        FunctionTool(optimize_routes),
    ],
)
