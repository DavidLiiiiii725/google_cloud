# Transport Agent — Agricultural Supply Chain

The Transport Agent consolidates many smallholder farms into efficient shared delivery
routes, and re-plans when a storm disrupts yields and roads. It is part of a three-agent
system (Farmer, Transport, Merchant) that coordinate through a shared MongoDB "blackboard."

The agent **reasons** (which stock can co-load, how to frame the problem, whether a plan is
sane) while a real **OR-Tools optimizer** does the routing math. That split is the point:
the reasoning is what makes it an agent, not just a solver.

## What's in here

```
transport/            The Transport Agent (FastAPI) — runs on port 8001
  backend/            agent logic: reasoning, pipeline, MongoDB read/write
  index.html          the dashboard UI (route map, reasoning trace, storm diff)
route-optimizer/      OR-Tools route solver (FastAPI) — runs on port 8080
  main.py             capacitated vehicle routing (CVRP)
  routes_client.py    optional Google Routes API for real road distances
shared/schemas/       the data contract (farms, transport_plans, world_events)
orchestrator/         fires the storm cascade for the demo
```

## Two services

The system runs as **two separate servers**, each in its own terminal and its own
virtual environment:

| Service         | Folder            | Port | Start command                          |
|-----------------|-------------------|------|----------------------------------------|
| Transport Agent | `transport/`      | 8001 | `uvicorn backend.main:app --port 8001` |
| Route Optimizer | `route-optimizer/`| 8080 | `uvicorn main:app --port 8080`         |

The UI (`transport/index.html`) talks to the agent on 8001; the agent calls the optimizer
on 8080. Both must be running to see optimized multi-stop routes. If the optimizer is down,
the agent falls back to a simple greedy router so the demo still works.

## Setup & run (Windows / PowerShell)

Run these in **two separate terminals**. Each service needs its own venv.

### Terminal 1 — Route Optimizer (port 8080)

```powershell
cd route-optimizer
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --port 8080
```

Leave it running. Confirm it's up: open http://localhost:8080/health → `{"ok": true}`.

**Optional — real road distances via Google Routes API.** Without this, the optimizer uses
straight-line distances (the demo works fine either way). To enable real road distances,
set the key in this terminal *before* starting the server:

```powershell
$env:GOOGLE_MAPS_API_KEY = "your-key-here"
uvicorn main:app --port 8080
```

(Requires the **Routes API** enabled and billing active on your Google Cloud project. Do
NOT enable "Route Optimization API" — that competes with OR-Tools.)

### Terminal 2 — Transport Agent (port 8001)

```powershell
cd transport
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:OPTIMIZER_URL = "http://localhost:8080"
uvicorn backend.main:app --port 8001
```

The `OPTIMIZER_URL` line connects the agent to the optimizer — set it *before* launching,
in this same terminal. On startup the agent auto-seeds 5 demo farms into an in-memory store
(no database required for a local demo).

### View the dashboard

Open `transport/index.html` in your browser. It connects to the agent on 8001.

## Running the demo

In the dashboard:

1. **BUILD PLAN** — the agent reads the farms, reasons about load compatibility (cold-chain
   vs ambient, fragile vs stackable), formulates the problem, calls OR-Tools, sanity-checks
   the result, and writes a consolidated plan. Watch the five reasoning stages light up.
2. **⚡ FIRE STORM** — a storm blocks two farms' roads and cuts yields. The agent re-reasons
   and re-plans: trucks collapse, blocked farms are flagged as unrouted, cost drops. The
   before/after diff is the headline.

The map shows farms as pins (red = storm-blocked) with each truck's route drawn in its own
color. The route list shows ordered stops with per-leg distance/time and per-truck totals.

## Key behaviors (PowerShell notes)

- Both servers read environment variables **at launch** — always set the env var first,
  then start the server, in the same terminal.
- A venv is tied to its absolute path; if you move the project folder, delete `.venv` and
  recreate it.
- `curl` in PowerShell is an alias — use `curl.exe` for real HTTP calls.

## Endpoints (for reference / scripting)

Transport Agent (8001):
- `POST /api/transport/plan` — build the initial consolidated plan
- `POST /api/transport/replan` — fire the storm and re-plan
- `POST /api/transport/seed` — reset to the clean 5-farm baseline
- `GET  /api/transport/plan/latest` — the current committed plan
- `GET  /api/transport/farms` — all farms (for the map)
- `GET  /api/transport/trace` — per-stage reasoning (the UI polls this)
- `GET  /api/transport/health` — status + db mode

Route Optimizer (8080):
- `POST /solve` — solve a routing problem (called by the agent)
- `GET  /health` — status

## Notes

- **No database needed for the local demo** — the agent uses an in-memory store when
  `MONGODB_URI` is unset. Set `MONGODB_URI` to connect to a shared MongoDB Atlas cluster
  (how the three agents share state in the full system).
- The optimizer needs the **Visual C++ Redistributable** on Windows for OR-Tools to load
  (https://aka.ms/vs/17/release/vc_redist.x64.exe) if you hit a `DLL load failed` error.
