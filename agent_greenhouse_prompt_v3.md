# Agent Greenhouse — Claude Code Prompt

## What you are building

A full-stack AI-powered greenhouse monitoring system for a hackathon demo. The centerpiece is a **pixel-art isometric greenhouse** rendered in an HTML5 canvas — think Stardew Valley meets a real IoT system. Users adjust environment sliders (temperature, humidity, CO₂, soil moisture per zone), the AI agent pipeline detects anomalies, calls Gemini for vision analysis, generates an action plan, and either auto-approves it or asks the human. Everything plays out visually in the canvas in real time.

The judges need to see: real Gemini API calls, real MongoDB reads/writes, a meaningful human-in-the-loop moment, and a canvas that looks alive.

---

## Tech stack

- **Frontend**: single `index.html`, no framework, no build step
- **Backend**: Python + FastAPI
- **AI**: Gemini 2.0 Flash — multimodal (vision) for monitoring, tool use for action planning
- **Database**: MongoDB Atlas
- **Storage**: Google Cloud Storage for plant images (fall back to local `/assets/` if unconfigured)
- **Font**: Press Start 2P (Google Fonts) for pixel-art labels

---

## Sensors and data model

The system simulates these sensors, stored as a single document per cycle in MongoDB `live_telemetry`:

**Air:** temperature (°C), humidity (%), CO₂ (ppm), and **VPD** (vapour pressure deficit in kPa — derived from temp + humidity before insert, the single best compound stress indicator)

**Light:** PAR/PPFD (µmol/m²/s — actual plant fuel, not just lux), lux, and daily light integral (DLI, accumulated mol/m²/day since midnight)

**Soil — per zone (seedling / growing / harvest):** moisture % (volumetric water content), soil temperature, electrical conductivity (EC in mS/cm)

**Water system:** tank level %, flow rate (L/min), solution pH and EC

**Actuator state:** vent position (0–100%), fan RPM, irrigation on/off, cooling on/off, light intensity %, CO₂ injection on/off

**Vision:** one GCS image URL per zone, updated each cycle. Gemini's health assessment written back into the same document after analysis.

Also maintain:
- `historical_climate` — 12 pre-seeded monthly baseline documents with mean/std/thresholds per sensor, used by the rules engine for z-score comparison
- `incidents` — one document per detected incident, tracks the full lifecycle from detection through action plan to approval and execution
- `agent_logs` — append-only log stream, one entry per reasoning step, used to drive the SSE terminal panel in the frontend

---

## Agent pipeline

When sensor data arrives, run this sequence as a background task (POST /api/sensor returns immediately):

1. **Rules engine** — compute z-scores against monthly baseline, flag anomalies, compute per-zone health (0–1). Zone 2 harvest is most sensitive, zone 0 seedling is most resilient. VPD is the most important compound signal — a simultaneous high-temp + low-humidity combination creates a dangerous VPD even if neither reading alone crosses a threshold. No LLM involved here.

2. **Confidence scoring** — produce a 0.0–1.0 confidence score from five weighted components: sensor deviation magnitude (z-score strength), sensor agreement (how many independent sensors agree), trend duration (spike vs sustained pattern), Gemini vision confirmation (filled in after step 3), and historical incident rate (has this pattern caused damage before).

3. **Monitoring agent (Gemini multimodal)** — if anomaly detected: send all three zone images + full sensor snapshot to Gemini 2.0 Flash. Ask it to confirm severity, identify which zones are affected, and state the root cause in plain English. Write vision assessment back into the telemetry doc. Recompute confidence score now that vision is available.

4. **Action agent (Gemini tool use)** — generate a multi-step mitigation plan using tools: open_vents, set_fan_speed, activate_irrigation (per zone), activate_cooling, adjust_lighting, inject_co2, schedule_recheck. Steps must be ordered by urgency. Risk-classify the overall plan as low (vents/fan/lights only) or medium (includes irrigation or cooling).

5. **Auto-approve engine** — decide whether to auto-approve, start a countdown, or require human input. See section below.

Stream every reasoning step to `agent_logs` as it happens so the frontend terminal updates in real time.

---

## Auto-approve logic

This is a first-class feature, not an afterthought. The decision is a function of **severity × confidence × action risk**, with hard override rules checked first.

**Override rules (block auto-approve regardless of score):**
- Tank level < 15% and plan includes irrigation → always HITL (pump damage risk)
- Night hours 22:00–06:00 and plan is medium/high risk → queue for morning
- Any core sensor data older than 2 cycles → stale data, refuse auto
- Gemini vision shows healthy plants but sensors say critical → sensor malfunction suspected, always HITL
- Same zone re-triggered within 30 min of a previous auto-approved action → previous action may have failed, force human review
- High-risk actions (emergency drain, full shutdown) → always HITL, no exceptions

**Confidence matrix (after overrides pass):**
- Warning severity + confidence ≥ 0.75 + low-risk action → auto-approve immediately
- Warning severity + confidence ≥ 0.75 + medium-risk → auto-approve after 60s countdown
- Critical severity + confidence ≥ 0.90 → auto-approve after 30s countdown, notify operator
- Critical severity + confidence < 0.90 → HITL, 10-min timeout then auto
- Catastrophic severity → always HITL, 5-min timeout then auto

**The countdown is the key demo moment.** When a 30s countdown starts, the UI shows a draining bar and a live timer. The operator can approve early or dismiss. If they do nothing, the system fires automatically and logs "Auto-approved after 30s — operator did not intervene." This is what makes the system feel genuinely autonomous without being reckless.

**Learning:** if an operator manually reverses a previous auto-approved action, raise the required confidence threshold for that action type by 0.10 for the next 24 hours.

---

## Frontend — pixel-art isometric greenhouse

Single `index.html`. The canvas is the star.

**Visual style:** pixel art, `ctx.imageSmoothingEnabled = false` always. 16×16 and 32×32 base sprites scaled 2×–3×. Limited colour palette (~20 colours, hardcoded hex — not CSS variables, because the game scene must not invert in dark mode). Press Start 2P font for gauge numbers and UI labels.

**Canvas scene:** isometric greenhouse viewed from above-front. Glass roof panels. Three distinct growing zones left to right — seedling (small sprouts), growing (mid-height plants), harvest (full plants with fruit/flowers). Equipment column on the far left: water pump with indicator light, CO₂ tank, circulation fan, control panel. Roof vent slats that physically rotate open. Grow light bar along the top ridge.

**Plants are drawn procedurally** — no image files needed for the plants. Stems, leaves, and fruit are pixel rectangles. Plants sway gently when healthy. As health drops: colour shifts from green → yellow-brown, leaves droop downward, fruit fades. Below 20% health, plants slump sideways.

**Environment affects the scene visually:**
- Sky colour shifts from cool blue → warm orange → hot red as temperature rises
- Soil tiles darken and shift blue-tint when irrigation is on
- Water droplet particles fall across all zones when irrigation is active
- Heat shimmer particles drift upward when temp > 36°C, intensity proportional to temperature
- Vent slats rotate to open position when vent_pct > 0
- Fan blades animate when cooling is on
- Grow light bar colour shifts warm/cool with light intensity setting

**Zone health bars** — 10 discrete pixel blocks per zone, displayed above each zone in-canvas. Blocks disappear as health drops. Colour: green above 60%, amber 30–60%, red below 30%.

**Zone sensitivity** — zone 2 (harvest) wilts first, zone 0 (seedling) wilts last. This is visible in the canvas and creates a natural story arc for the demo.

**Right panel (HTML, not canvas):**
- Retro LCD gauges for temperature, humidity, VPD, CO₂, PAR, and tank level. VPD gets a "plant stress index" subtitle — it's the key indicator evaluators won't know about.
- Zone health bars (seedling / growing / harvest) with numeric %
- Incident card — appears on anomaly. Shows Gemini's root cause in plain English, action plan steps, confidence score, and the countdown bar if auto-approve is running. Pixel-art styled Approve / Dismiss buttons (chunky 3px border, translate 2px on press).

**Bottom panel:**
- Agent log terminal — dark background, monospace green text, colour-coded tags: `[DETECT]` cyan, `[REASON]` amber, `[HITL]` purple, `[ACT]` green, `[AUTO]` white. Streams via SSE.
- Environment sliders: temperature, humidity, CO₂, PAR, zone moisture × 3, tank level. Each slider POSTs to `/api/sensor` with 800ms debounce.
- "Stress Test" button — sets temp=44°C, humidity=22%, all zone moisture=15% simultaneously. Triggers a critical incident within 3 seconds for reliable demo triggering.
- Reset button — returns everything to baseline, clears open incidents.

---

## API surface

```
POST /api/sensor          ← sliders post here, returns immediately, pipeline runs in background
GET  /api/stream/logs     ← SSE, streams agent_log entries in real time
GET  /api/status          ← zone health, actuator state, severity, countdown remaining
GET  /api/incident/latest ← current open incident + action plan, null if none
POST /api/incident/{id}/approve
POST /api/incident/{id}/dismiss
GET  /api/history         ← last 60 readings for sparkline charts
POST /api/reset           ← demo convenience
```

---

## Resilience rules (non-negotiable for demo)

- If `GEMINI_API_KEY` is missing or rate-limited → replay the last successful Gemini response cached by severity level. Never crash, never show an error to the evaluator.
- If GCS is unconfigured → select a local image from `/assets/` based on stress level (healthy / stressed / wilting). Never block on this.
- All Gemini calls wrapped in try/except → on failure, log `[REASON] Vision unavailable — using sensor data only` and continue the pipeline.
- `POST /api/sensor` must return in under 200ms always.
- CORS: `allow_origins=["*"]`.

---

## Demo script (what the judges will see)

1. Greenhouse opens healthy — all zones green, log says "Monitoring 3 zones — nominal"
2. Drag temperature to 44°C and humidity to 22° — harvest zone immediately starts wilting, sky warms, heat shimmer appears
3. Agent log streams live: anomaly flagged → Gemini called → root cause → confidence 0.91
4. Incident card appears with action plan + 30s countdown bar draining
5. Countdown hits zero → auto-approved → vents open, irrigation starts, fan spins, log shows `[AUTO]`
   OR operator clicks Approve before countdown ends — same result, immediate
6. Plants slowly recover over 15 seconds
7. Log ends with "Re-check scheduled in 30 minutes"
8. Reset button → back to baseline, ready to repeat

---

## Definition of done

- Sliders update plants within 2 seconds
- Gemini vision analysis is a real API call and the response text appears in the log
- Auto-approve countdown is visible and fires correctly
- Approve button triggers visible canvas changes (vents, water, fan)
- Zone 2 wilts before zones 0 and 1
- VPD gauge updates when temp or humidity changes
- SSE log streams with correct tag colours in real time
- Stress Test button reliably triggers a critical incident
- Reset works cleanly
- Everything starts with `uvicorn backend.main:app --reload` + opening `index.html`
