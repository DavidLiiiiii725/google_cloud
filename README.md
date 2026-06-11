# Agent Farm

**Three cooperating agents — Greenhouse, Transport, Merchant — sharing one MongoDB
blackboard.** No agent calls another in process; each reads the documents it depends on
and writes its own. Gemini does the reasoning, MongoDB is the shared state, accessed
through the MongoDB MCP server.

🔗 **Live demo:** https://agent-farm-582643636508.us-central1.run.app

- **Greenhouse Agent** monitors a pixel-art greenhouse and publishes its harvestable
  yield as a `farms` document.
- **Transport Agent** reads the live `farms`, reasons about co-loadability, calls an
  OR-Tools optimizer, and writes a consolidated `transport_plans` doc.
- **Merchant Agent** reads the committed `transport_plans` (which farms actually got
  routed) joined with `farms` (their crops), allocates the arriving supply across
  competing buyers by priority, prices the shortfall, and writes a `market_orders` doc.

A storm cascades through all three: the Transport Agent's storm button blocks roads +
reduces yields via a `world_events` doc → the Greenhouse Agent's `farm_publish` reacts
and lowers its yield → the Transport Agent re-plans against the new picture → the
Merchant Agent **auto-reallocates** (it watches the blackboard) and applies scarcity
pricing. The **Coordination** tab tracks all three in real time, and a Gemini ↔
MongoDB-MCP situation report narrates the whole chain.

## Architecture

```
        ┌────────────────────────────────────────────────────────────┐
        │                 MongoDB  (shared blackboard)                │
        │  farms · world_events · transport_plans · market_orders     │
        │  buyers · agent_logs · live_telemetry · incidents           │
        └──▲────────────▲─────────────────▲────────────────▲──────────┘
   farms / │            │ plans /         │ plans (read)   │ market_orders /
   yield   │            │ world_events    │                │ buyers
           │            │                 │                │
  ┌────────┴───────┐ ┌──┴─────────────┐ ┌─┴────────────┐ ┌─┴───────────────┐
  │ Greenhouse     │ │ Transport      │ │ Route        │ │ Merchant        │
  │  port 8000     │←│  port 8001     │→│ Optimizer    │ │  port 8002      │
  │  + serves UI   │ │                │ │  port 8080   │ │  (auto-watcher) │
  │  + Gemini↔MCP  │ │                │ │ (pure math)  │ │  + Gemini       │
  └────────────────┘ └────────────────┘ └──────────────┘ └─────────────────┘

  The unified UI (served by 8000) has four tabs:
    🛰️ COORDINATION — live Greenhouse→Transport→Merchant flow + MCP situation report
    🌱 GREENHOUSE   — pixel-art scene + agent reasoning + sliders
    🚛 LOGISTICS    — farms map + reasoning trace + transport plan
    🏪 MARKET       — buyer demand book + allocation table + scarcity pricing
```

## Tech stack

- **Gemini** (via **Vertex AI**) — agent reasoning, plant-health vision, situation report
- **MongoDB Atlas** — the shared blackboard, accessed through the **MongoDB MCP server**
- **Google OR-Tools** — vehicle-routing optimization
- **FastAPI** services + a vanilla HTML5-canvas frontend, deployed on **Google Cloud Run**

## Requirements

- **Python 3.12** (the pinned dependencies — pydantic-core, Pillow, OR-Tools — ship
  prebuilt wheels for 3.12; newer Python versions may try to build from source and fail)
- **Node.js 18+** (the MongoDB MCP server runs via `npx mongodb-mcp-server`)
- A **MongoDB Atlas** cluster (connection string)
- A **Google Cloud project** with Vertex AI enabled (for Gemini)

## Setup

```powershell
# 1. Create venv with Python 3.12 & install deps
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Authenticate for Vertex AI / Gemini (uses your own GCP project)
gcloud auth application-default login
gcloud config set project YOUR_GCP_PROJECT_ID

# 3. Copy the env template and fill in your values
copy .env.example .env
```

Then edit `.env` with your own values (see `.env.example` for the full list). The key ones:

```
MONGODB_URI=<your Atlas connection string>     # the shared blackboard
MONGODB_DB=agent_greenhouse
GCP_PROJECT=YOUR_GCP_PROJECT_ID               # for Gemini via Vertex
GCP_REGION=us-central1
GEMINI_MODEL=gemini-2.5-flash
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=<your GCP project id>
GOOGLE_CLOUD_LOCATION=us-central1
```

> **Note:** with `MONGODB_URI` set, all agents share state through Atlas (the blackboard
> the cascade depends on). Without it, each process falls back to an in-memory store — the
> UI still works inside each tab, but the cascade won't bridge across processes.

## Run

Start the four services, each in its own terminal (venv activated). They connect to the
Atlas cluster from your `.env`:

```powershell
python -m uvicorn optimizer.main:app --port 8080
```
```powershell
$env:OPTIMIZER_URL="http://localhost:8080"
python -m uvicorn app.transport.main:app --port 8001
```
```powershell
python -m uvicorn app.merchant.main:app --port 8002
```
```powershell
python -m uvicorn app.greenhouse.main:app --port 8000
```

Then open **http://localhost:8000** — the unified UI loads on the Greenhouse tab. Click
`🛰️ COORDINATION` to watch all three agents, or `🚛 LOGISTICS` / `🏪 MARKET` for each in detail.

> The included `run.ps1` / `run.bat` scripts start a **local** mongod instead of Atlas and
> are intended for offline development. For Atlas (the deployed configuration), run the
> services directly as shown above.

## Demo flow

1. **Greenhouse tab** — drag temperature to 44 °C, humidity to 22%. Plants wilt,
   the incident pipeline fires, the plan auto-approves, actuators recover the scene.
   Behind the scenes, every cycle the Greenhouse upserts its `farms` doc with the
   live yield (scaled by harvest-zone health).
2. **Logistics tab** — see all farms on the blackboard. The greenhouse's farm
   shows up flagged with `▣` and is highlighted on the map in cyan. Click
   `BUILD PLAN` to consolidate them into multi-stop routes.
3. **Market tab** — the Merchant Agent has already allocated the routed supply across
   four buyers (hospital → school → market → exporter). See per-buyer fill rates, the
   allocation/pricing table, and a Gemini-written rationale.
4. **⚡ FIRE STORM** (Logistics tab) — writes an active `world_events` doc, blocks
   two peer farms, drops yields. The Greenhouse Agent reads the event and republishes a
   reduced farm doc; the Transport Agent re-plans; **the Merchant Agent auto-reallocates**
   (it watches the blackboard) — fill rate falls, essentials are protected, scarce crops
   get re-priced. The before/after diffs in both Logistics and Market are the headline.
5. **Coordination tab** — the live flow diagram shows the cascade rippling through all
   three nodes. Click **ASK GEMINI** for a MongoDB-MCP situation report that reads every
   collection and summarizes the whole chain (`CHAIN: HEALTHY` / `DISRUPTED`).
6. **↺ CLEAR STORM** — resolves the world event, reopens roads, re-plans clean; the
   Merchant reallocates back to full fulfillment.

## Repo layout

```
app/
  greenhouse/   greenhouse FastAPI service (port 8000), serves web/index.html
    farm_publish.py   ← the bridge to the blackboard's `farms` collection
    coordination.py   ← cross-agent snapshot + Gemini↔MCP situation report
  transport/    transport FastAPI service (port 8001)
  merchant/     merchant FastAPI service (port 8002) — allocation + scarcity pricing
    agent.py          ← priority fair-share allocation brain
    pipeline.py       ← the blackboard watcher that makes the cascade automatic
  mcp_mongo.py  Gemini ↔ MongoDB MCP bridge (shared)
optimizer/      OR-Tools microservice (port 8080) — pure math, no DB
orchestrator/   standalone script that fires the storm cascade
shared/schemas/ JSON schemas for the blackboard contracts (incl. market_orders)
web/            unified UI (one index.html: Coordination + Greenhouse + Logistics + Market)
assets/         pixel-art plant images
docs/           greenhouse_change.md, transport_setup.md (design notes)
Dockerfile      Cloud Run container (all services + nginx + Node for the MCP server)
deploy/         nginx.conf + start.sh used by the container
```

## Deployment (Google Cloud Run)

The app deploys as a single Cloud Run service (all four services + nginx in one
container; the MCP server runs via Node inside it). Configuration is passed as
environment variables (see `.env.example` for the list).

```powershell
gcloud run deploy agent-farm --source . --region us-central1 `
  --allow-unauthenticated --port 8080 --memory 2Gi --timeout 3600 `
  --env-vars-file env.yaml
```

The Cloud Run service account needs the **Vertex AI User** role, and the Atlas cluster's
Network Access must allow connections from anywhere (`0.0.0.0/0`) so the container can reach it.

## API surface

**Greenhouse (8000)**

- `POST /api/sensor` — sliders post here, pipeline runs in the background
- `GET  /api/status`, `/api/history`, `/api/incident/latest`, `/api/agent/trace`
- `POST /api/incident/{id}/approve`, `/dismiss`, `/api/reset`
- `GET  /api/stream/logs` — SSE
- `GET  /api/farm/state`, `POST /api/farm/publish` — blackboard introspection

Plus the cross-agent coordination layer (served by 8000):

- `GET  /api/coordination/state` — one blackboard snapshot across all three agents
- `POST /api/coordination/narrate` — Gemini reads every collection via MongoDB MCP and
  writes a situation report on the cascade

**Transport (8001)**

- `POST /api/transport/plan`, `/replan`, `/seed`
- `GET  /api/transport/farms`, `/plan/latest`, `/trace`
- `GET  /api/transport/world`, `POST /api/transport/world/clear`
- `GET  /api/transport/stream/logs` — SSE

**Merchant (8002)**

- `GET  /api/merchant/orders/latest`, `/buyers`, `/supply`, `/trace`, `/world`
- `POST /api/merchant/allocate` — run an allocation now; `/seed` — reset the demand book
- `GET  /api/merchant/stream/logs` — SSE
- Runs a background watcher: a new committed `transport_plan` or a storm auto-triggers
  a reallocation — no manual step needed.

**Optimizer (8080)**

- `POST /solve` — called by the Transport Agent; takes a CVRP problem, returns routes
- `GET  /health`

## License

MIT — see [LICENSE](LICENSE).
