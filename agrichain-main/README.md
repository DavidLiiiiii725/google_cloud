# Agricultural Supply Chain — Multi-Agent System

Three agents adapting to a storm, coordinating through a MongoDB "blackboard."
No agent calls another in-process; each reads the documents it depends on and writes its own.

- **Farmer Agent** (`greenhouse/`) — adaptive greenhouse control + revised-yield reporting.
- **Transport Agent** (`transport/` + `route-optimizer/`) — consolidates smallholders into
  shared routes; re-routes when the storm changes yields and roads. Reasons (compatibility,
  formulation, sanity-checking) while OR-Tools does the routing math.
- **Merchant Agent** — reallocates limited supply across buyers, adjusts pricing on shortfall.

The storm in `world_events` ripples: Farmer lowers yields → Transport re-plans → Merchant
reallocates. The visible before/after diff in `transport_plans` is the demo centerpiece.

## Repo layout

```
shared/schemas/      data contract (LOCK DAY 1) — schemas + example docs
greenhouse/          Farmer Agent (the original repo, moved here)
transport/           Transport Agent backend
route-optimizer/     OR-Tools service (deploys to Cloud Run separately)
orchestrator/        fires the storm cascade for the demo
ui/                  (optional) unified dashboard; greenhouse/index.html still works solo
```

## Run locally (in-memory mode — no Atlas needed for a smoke test)

```bash
# Transport Agent
cd transport && pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8001

# (separately) OR-Tools optimizer
cd route-optimizer && pip install -r requirements.txt
uvicorn main:app --reload --port 8080
# then set OPTIMIZER_URL=http://localhost:8080 in transport/.env

# (separately) Greenhouse — unchanged from the original
cd greenhouse && pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

With all three pointed at the SAME `MONGODB_URI`, fire the cascade:

```bash
MONGODB_URI=... TRANSPORT_URL=http://localhost:8001 python -m orchestrator.storm
```

## The git merge (Path A — additive, low-conflict)

The work is almost entirely new folders, so do it as one additive branch rather than
merging divergent histories.

```bash
# 1. From the existing repo root, branch off main
git checkout -b feat/transport-agent

# 2. Move the current app into greenhouse/ (one-time restructure)
mkdir greenhouse
git mv backend greenhouse/backend
git mv assets greenhouse/assets
git mv index.html greenhouse/index.html
git mv requirements.txt greenhouse/requirements.txt
git mv agent_greenhouse_prompt_v3.md greenhouse/

# 3. Drop in the new folders (from this merge package)
#    shared/  transport/  route-optimizer/  orchestrator/
git add shared transport route-optimizer orchestrator README.md

# 4. Apply the greenhouse change (see greenhouse_change.md):
#    add farm_publish.py, two db.py accessors, two publish_farm() calls
git add greenhouse/backend/farm_publish.py greenhouse/backend/db.py greenhouse/backend/pipeline.py

# 5. Commit + open a PR
git commit -m "Add Transport Agent + OR-Tools optimizer + shared schema contract (blackboard)"
git push -u origin feat/transport-agent
```

The only edits to *existing* files are in `greenhouse/backend/db.py` and `pipeline.py`
(a handful of lines, described in `greenhouse_change.md`). Everything else is new files,
so conflicts with your teammates' work are minimal. Coordinate the `git mv` restructure
(step 2) with them first — that's the one change that touches paths everyone references.

## Build sequence (your two weeks)

- **Day 1** — lock `shared/schemas/` (done); MongoDB MCP reads `farms` as the smoke test.
- **Day 4** — `route-optimizer/` working standalone (swap the skeleton CVRP for full OR-Tools).
- **Day 6** — wire optimizer as a tool (`optimizer_client.solve`) + write agent reasoning in `agent.py`.
- **Day 8** — storm re-plan path (`pipeline.replan`) — already scaffolded and tested.
- **Day 9** — surface the trace + diff in the UI (poll `/api/transport/trace`, `/api/transport/plan/latest`).
- **Day 11** — harden edge cases (blocked roads, empty plan, optimizer timeout — fallbacks in place).
- Stretch only with time to spare: driver scheduling, multi-objective tuning, one negotiation exchange.
- **Never cut the storm re-plan.**
```
