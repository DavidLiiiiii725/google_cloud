# Agent Farm

**Three cooperating agents — Greenhouse, Transport, Merchant — sharing one MongoDB
blackboard.** No agent calls another in process; each reads the documents it depends on
and writes its own. Gemini does the reasoning, MongoDB is the shared state.

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

## Setup

```powershell
# 1. Create venv & install deps (one-time)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. (Optional but recommended) Sign in for Vertex AI / Gemini calls
gcloud auth application-default login
gcloud config set project centered-radio-497405-u9

# 3. Copy .env.example to .env and fill in MONGODB_URI
copy .env.example .env
#  - With MONGODB_URI set, both agents share state through Atlas (the blackboard).
#  - With it unset, each process uses its own in-memory store — UI still works
#    inside each tab, but the cascade doesn't bridge processes.
```

## Run

```powershell
.\run.ps1
```

This opens three background windows (optimizer + transport + merchant) and runs the
greenhouse in the foreground. Open **[http://localhost:8000](http://localhost:8000)** in a browser — the
unified UI loads on the Greenhouse tab. Click `🛰️ COORDINATION` to watch all three
agents, or `🚛 LOGISTICS` / `🏪 MARKET` for each agent in detail.

## Demo flow

1. **Greenhouse tab** — drag temperature to 44 °C, humidity to 22%. Plants wilt,
  incident pipeline fires, plan auto-approves, actuators recover the scene.
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
```

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

