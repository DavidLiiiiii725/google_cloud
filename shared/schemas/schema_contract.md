# Data Contract — Agricultural Supply Chain Multi-Agent System

**Lock this Day 1.** The schemas are the API between three independently-deployed agents that
share only a MongoDB Atlas database (the "blackboard"). No agent calls another in-process;
each reads the documents it depends on and writes its own. Changes after Day 1 require a sync
between all three of you.

## Conventions (matched to the existing greenhouse codebase)

- **String `_id`s**, human-readable and prefixed: `farm-eldoret-01`, `plan-7f3a9c`, `evt-storm-01`.
  (The repo's `db._MemoryCollection` and Atlas both treat `_id` as an opaque string — keep it that way
  so the in-memory fallback works for offline demos.)
- **Timestamps**: float Unix epoch seconds (`time.time()`), matching `live_telemetry.ts` and the
  `events.emit` log format already in use. (Not ISO strings — consistency with the existing repo wins.)
- **Money**: integer **cents** of one currency. Avoid floats for cost.
- **Units**: weight **kg**, volume **liters (L)**, distance **meters**, duration **seconds**.
- **`scenario_id`** on every doc so a demo can hold parallel worlds (`baseline` vs `post-storm`)
  without collisions. Default `"default"`.
- **`updated_at`** (float epoch) + **`updated_by`** (`"farmer"｜"transport"｜"merchant"｜"orchestrator"`)
  on every doc.

## Write ownership (strict — this is the blackboard discipline)

| Collection        | Writer        | Readers                          |
|-------------------|---------------|----------------------------------|
| `farms`           | Farmer Agent  | Transport Agent, Merchant Agent  |
| `transport_plans` | **Transport** | Merchant Agent, demo UI          |
| `allocations`     | Merchant      | demo UI                          |
| `world_events`    | Orchestrator  | all three agents                 |

Your Transport Agent **reads** `farms` + the active `world_event`, and **writes** `transport_plans`.
You never write `farms`. The Merchant Agent never writes `transport_plans`. Keep it strict.

## How the greenhouse repo participates

The existing greenhouse app currently writes `live_telemetry` / `incidents`. To join the supply chain
it gains exactly one new responsibility: **after an incident is resolved (yields known), upsert a
`farms` document** for its farm with the revised harvestable quantity. That's the only change to the
greenhouse agent's behavior. Everything else it already does stays as-is.

A storm in `world_events` is what makes the greenhouse re-run its pipeline (lower yield), which changes
the `farms` docs, which is what your Transport Agent detects to trigger the re-plan. That is the visible
cascade.

## The cascade (what the orchestrator fires)

1. Orchestrator writes a `world_events` doc: `{type:"storm", status:"active", ...}`.
2. Farmer Agent(s) react → revise their `farms` doc: `yield_status:"reduced"`, lower `quantity_kg`,
   possibly `access:"blocked"`.
3. Transport Agent detects changed `farms` (or is signaled) → re-reasons compatibility, re-formulates,
   re-calls the optimizer, writes a **new** `transport_plans` doc tagged `replan_of: <prior_plan_id>`.
4. Merchant Agent reads the new plan → reallocates supply, adjusts pricing.

The before/after diff between the two `transport_plans` docs (5 stops/2 trucks → 3 stops/1 rerouted
truck, with cost delta) is your demo centerpiece.
