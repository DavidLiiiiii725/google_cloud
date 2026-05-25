"""Pipeline orchestration: rules → confidence → vision → action → decide → execute.
Each stage is reported to workflow.tracker so the frontend can show the agent thinking
in real time. Small `await asyncio.sleep` calls between stages make the workflow
legible to a human audience without slowing the demo meaningfully."""
from __future__ import annotations
import asyncio
import time
import uuid
from . import db, state, events, rules, confidence, gemini_client, images, approve, workflow

# Per-stage pause so the audience can read each step lighting up.
# 0 to disable. 0.4s × 5 stages ≈ 2s added — well within demo budget.
STAGE_PAUSE = 0.45

# After a plan finishes executing, suppress new pipelines for this long.
# Lets the audience see the completed trace and the environment recover
# before the system decides whether to re-engage.
POST_EXEC_COOLDOWN = 12.0
_last_execution_at = 0.0


async def execute_plan(incident_id: str, source: str):
    """Apply each step of the plan to actuator/sensor state."""
    inc = db.incidents().find_one({"_id": incident_id})
    if not inc:
        events.emit("ACT", f"No incident {incident_id} to execute")
        return
    plan = inc.get("plan", {})
    steps = plan.get("steps", [])
    workflow.tracker.set("execute", "running",
                          message=f"Executing {len(steps)} steps ({source})",
                          data={"source": source, "steps_total": len(steps), "steps_done": 0,
                                "steps": steps})
    events.emit("ACT", f"Executing plan ({source}) — {len(steps)} steps", incident_id=incident_id)
    applied = []
    for i, step in enumerate(steps, start=1):
        tool, args = step.get("tool"), step.get("args", {})
        _apply_tool(tool, args)
        applied.append({"tool": tool, "args": args})
        events.emit("ACT", f"→ {tool}({args})", incident_id=incident_id)
        workflow.tracker.set("execute", "running",
                              message=f"step {i}/{len(steps)} · {tool}",
                              data={"source": source, "steps_total": len(steps), "steps_done": i,
                                    "steps": steps, "applied": applied})
        await asyncio.sleep(0.4)
    db.incidents().update_one({"_id": incident_id}, {"$set": {
        "status": f"{source}_approved" if source in ("auto_timeout", "auto_immediate") else "operator_approved",
        "executed_ts": time.time(),
    }})
    workflow.tracker.set("execute", "done",
                          message=f"{len(steps)} steps applied via {source}",
                          data={"source": source, "steps_total": len(steps), "steps_done": len(steps),
                                "steps": steps, "applied": applied})
    global _last_execution_at
    _last_execution_at = time.time()
    state.set_incident_id(None)
    approve.cancel_countdown(incident_id)


def _apply_tool(tool: str, args: dict):
    """Each tool turns an actuator ON. The continuous environmental response is handled
    by apply_actuator_physics() on every heartbeat tick — not by one-shot deltas here."""
    if tool == "open_vents":
        state.update_actuator(vent_pct=float(args.get("percent", 50)))
    elif tool == "set_fan_speed":
        state.update_actuator(fan_rpm=float(args.get("rpm", 1000)))
    elif tool == "activate_irrigation":
        zone = args.get("zone", "growing")
        irr = dict(state.get_actuator().irrigation)
        irr[zone] = True
        state.update_actuator(irrigation=irr)
    elif tool == "activate_cooling":
        state.update_actuator(cooling=True)
    elif tool == "adjust_lighting":
        state.update_actuator(light_pct=float(args.get("percent", 70)))
    elif tool == "inject_co2":
        state.update_actuator(co2_inject=True)
    elif tool == "schedule_recheck":
        events.emit("INFO", f"Re-check scheduled in {args.get('minutes', 30)} minutes")


# Baselines that the greenhouse drifts toward when actuators are working
BASELINE = {
    "temperature": 24.0,
    "humidity": 65.0,
    "co2": 800.0,
    "moisture": 60.0,
}


def apply_actuator_physics():
    """Simulate environmental response to current actuator state. Runs every heartbeat tick.
    Sensors drift toward their baseline at a rate that depends on which actuators are on."""
    actuator = state.get_actuator()
    sensors = state.get_sensors()
    patch: dict = {}

    # ----- Temperature -----
    cur_t = sensors["temperature"]
    rate = 0.04  # baseline passive drift toward target
    if actuator.cooling:           rate += 0.28
    if actuator.vent_pct > 0:      rate += 0.10 * (actuator.vent_pct / 100.0)
    if actuator.fan_rpm > 0:       rate += 0.04 * (actuator.fan_rpm / 3000.0)
    patch["temperature"] = cur_t + (BASELINE["temperature"] - cur_t) * rate

    # ----- Humidity -----
    cur_h = sensors["humidity"]
    if actuator.cooling:
        h_rate = 0.25  # cooling raises humidity quickly
    elif actuator.vent_pct > 0 or actuator.fan_rpm > 0:
        h_rate = 0.18
    else:
        h_rate = 0.04
    patch["humidity"] = cur_h + (BASELINE["humidity"] - cur_h) * h_rate

    # ----- Per-zone moisture -----
    for zone in ("seedling", "growing", "harvest"):
        cur_m = sensors[f"moisture_{zone}"]
        target = BASELINE["moisture"]
        if actuator.irrigation.get(zone):
            # Drive up toward target quickly
            if cur_m < target + 15:
                patch[f"moisture_{zone}"] = min(80.0, cur_m + (target + 5 - cur_m) * 0.32)
            else:
                patch[f"moisture_{zone}"] = cur_m  # don't over-water
        else:
            # Slow evaporation toward baseline
            patch[f"moisture_{zone}"] = max(8.0, cur_m + (target - cur_m) * 0.03 - 0.25)

    # ----- CO2 -----
    cur_c = sensors["co2"]
    if actuator.co2_inject:
        patch["co2"] = min(1500.0, cur_c + 60.0)
    else:
        patch["co2"] = cur_c + (BASELINE["co2"] - cur_c) * 0.08

    # ----- Tank drain when irrigating -----
    any_irr = any(actuator.irrigation.values())
    if any_irr:
        patch["tank_level"] = max(0.0, sensors["tank_level"] - 0.4)
        patch["flow_rate"] = 1.8
    else:
        patch["flow_rate"] = 0.0

    state.update_sensors(patch)


_nominal_streak = 0


def auto_shutoff_actuators(severity: str):
    """When the greenhouse has been nominal for a couple of ticks, taper actuators off
    so the environment settles instead of overshooting. Called every cycle."""
    global _nominal_streak
    if severity != "nominal":
        _nominal_streak = 0
        return
    _nominal_streak += 1
    if _nominal_streak < 2:
        return  # let things settle for one more tick before shutting off

    actuator = state.get_actuator()
    if not (actuator.cooling or actuator.vent_pct > 0 or actuator.fan_rpm > 0
            or any(actuator.irrigation.values()) or actuator.co2_inject):
        return

    if actuator.cooling:
        state.update_actuator(cooling=False)
        events.emit("ACT", "Cooling off — conditions nominal")
    if any(actuator.irrigation.values()):
        state.update_actuator(irrigation={k: False for k in actuator.irrigation})
        events.emit("ACT", "Irrigation stopped — moisture restored")
    if actuator.co2_inject:
        state.update_actuator(co2_inject=False)
        events.emit("ACT", "CO₂ injection stopped")
    # Vents and fan taper gradually
    if actuator.vent_pct > 0:
        new_pct = max(0.0, actuator.vent_pct - 35.0)
        state.update_actuator(vent_pct=new_pct)
        if new_pct == 0:
            events.emit("ACT", "Vents closed")
    if actuator.fan_rpm > 0:
        new_rpm = max(0.0, actuator.fan_rpm - 900.0)
        state.update_actuator(fan_rpm=new_rpm)
        if new_rpm == 0:
            events.emit("ACT", "Fan off")


async def run_cycle(stress_test: bool = False):
    """One full pipeline cycle. Called by POST /api/sensor as a background task."""
    try:
        if stress_test:
            state.update_sensors({"temperature": 44.0, "humidity": 22.0,
                                  "moisture_seedling": 15.0, "moisture_growing": 15.0, "moisture_harvest": 15.0})
            events.emit("DETECT", "Stress test triggered — extreme conditions injected")

        # 0. PHYSICS — apply actuator effects continuously so sensors drift toward nominal
        #    whenever the agent's plan is in effect (cooling, vents, irrigation, etc).
        apply_actuator_physics()

        sensors = state.get_sensors()
        sensors["ts"] = time.time()

        # 1. Rules engine
        assessment = rules.assess(sensors)
        sev = assessment["severity"]

        # Write telemetry doc
        actuator = state.get_actuator()
        tele = {
            "ts": sensors["ts"],
            "temperature": sensors["temperature"], "humidity": sensors["humidity"],
            "co2": sensors["co2"], "vpd": assessment["vpd"],
            "par": sensors["par"], "lux": assessment["lux"], "dli": assessment["dli"],
            "tank_level": sensors["tank_level"], "flow_rate": sensors["flow_rate"],
            "ph": sensors["ph"], "solution_ec": sensors["solution_ec"],
            "soil": {z: {"moisture": sensors[f"moisture_{z}"],
                          "soil_temp": sensors[f"soil_temp_{z}"],
                          "ec": sensors[f"ec_{z}"]} for z in ("seedling", "growing", "harvest")},
            "actuator": actuator.model_dump(),
            "zone_health": assessment["zone_health"],
            "severity": sev,
        }
        db.live_telemetry().insert_one(tele)

        if sev == "nominal":
            auto_shutoff_actuators(sev)
            # If we had an active incident, mark it stale
            if state.get_incident_id():
                events.emit("INFO", "Conditions returned to nominal — closing prior incident")
                db.incidents().update_one({"_id": state.get_incident_id()}, {"$set": {"status": "resolved"}})
                state.set_incident_id(None)
            return
        else:
            auto_shutoff_actuators(sev)  # resets streak counter

        # Skip if there's already an active incident for the same severity tier
        if state.get_incident_id():
            # Update the rules stage with latest readings, then bail
            workflow.tracker.set("rules", "done",
                                 message=f"{len(assessment['anomalies'])} anomalies · {sev} (existing incident)",
                                 data={"anomalies": assessment["anomalies"], "severity": sev,
                                       "vpd": assessment["vpd"]})
            return

        # Post-execution cooldown: don't re-engage immediately after a plan finished.
        # This lets the audience see the completed trace and the environment recover.
        if _last_execution_at and (time.time() - _last_execution_at) < POST_EXEC_COOLDOWN:
            return

        # ------- New incident pipeline starts here -------
        workflow.tracker.start()
        events.emit("DETECT", f"Anomalies detected: {len(assessment['anomalies'])} (severity={sev})")
        for a in assessment["anomalies"][:5]:
            events.emit("DETECT", f"  {a['sensor']}={a['value']} z={a['z']} sev={a['severity']}")

        # ── STAGE 1: RULES ────────────────────────────────────────────────
        workflow.tracker.set("rules", "done",
                             message=f"{len(assessment['anomalies'])} anomalies · {sev}",
                             data={"anomalies": assessment["anomalies"], "severity": sev,
                                   "vpd": assessment["vpd"]})
        await asyncio.sleep(STAGE_PAUSE)

        # ── STAGE 2: CONFIDENCE (pre-vision) ──────────────────────────────
        workflow.tracker.set("confidence", "running", message="Scoring pre-vision confidence…")
        await asyncio.sleep(STAGE_PAUSE * 0.5)
        conf_pre = confidence.score(assessment, vision=None)
        workflow.tracker.set("confidence", "done",
                             message=f"pre-vision score · {conf_pre['score']}",
                             data={"score": conf_pre["score"], "components": conf_pre["components"],
                                   "phase": "pre-vision"})
        events.emit("REASON", f"Pre-vision confidence={conf_pre['score']}")
        await asyncio.sleep(STAGE_PAUSE)

        # ── STAGE 3: VISION (Gemini multimodal) ───────────────────────────
        workflow.tracker.set("vision", "running",
                             message="Calling Gemini 2.0 Flash with 3 zone images + snapshot…",
                             data={"images_sent": 3, "model": "gemini-2.0-flash-001"})
        events.emit("REASON", "Calling Gemini vision with zone images + snapshot…")
        img_paths = images.image_paths_for(assessment["zone_health"])
        vision = gemini_client.vision_assess(tele, img_paths, sev)
        workflow.tracker.set("vision", "done",
                             message=vision.get("root_cause", "?")[:200],
                             data={**vision,
                                   "images_sent": 3,
                                   "image_paths": [p.name for p in img_paths]})
        events.emit("REASON", f"Vision: {vision.get('root_cause', '?')[:140]}")
        await asyncio.sleep(STAGE_PAUSE)

        # Recompute confidence post-vision and update the stage
        conf_post = confidence.score(assessment, vision=vision)
        workflow.tracker.set("confidence", "done",
                             message=f"post-vision score · {conf_post['score']} (was {conf_pre['score']})",
                             data={"score": conf_post["score"], "components": conf_post["components"],
                                   "phase": "post-vision",
                                   "pre_vision_score": conf_pre["score"]})
        events.emit("REASON", f"Post-vision confidence={conf_post['score']}")
        await asyncio.sleep(STAGE_PAUSE * 0.5)

        # ── STAGE 4: ACTION PLAN (Gemini tool use) ─────────────────────────
        workflow.tracker.set("action", "running",
                             message="Generating mitigation plan via tool use…",
                             data={"model": "gemini-2.0-flash-001"})
        events.emit("REASON", "Generating action plan via tool use…")
        plan = gemini_client.action_plan(tele, vision, sev)
        workflow.tracker.set("action", "done",
                             message=f"{len(plan.get('steps', []))} steps · {plan.get('risk', '?')} risk",
                             data=plan)
        events.emit("REASON", f"Plan ({plan.get('risk', '?')} risk): {plan.get('summary', '')} — {len(plan.get('steps', []))} steps")
        await asyncio.sleep(STAGE_PAUSE)

        # Create incident document
        incident_id = f"inc-{uuid.uuid4().hex[:8]}"
        incident = {
            "_id": incident_id,
            "ts": time.time(),
            "severity": sev,
            "anomalies": assessment["anomalies"],
            "affected_zones": vision.get("affected_zones", []),
            "vision": vision,
            "plan": plan,
            "confidence": conf_post["score"],
            "confidence_components": conf_post["components"],
            "status": "open",
        }
        db.incidents().insert_one(incident)
        state.set_incident_id(incident_id)
        workflow.tracker.attach_incident(incident_id, sev)

        # ── STAGE 5: DECISION ─────────────────────────────────────────────
        workflow.tracker.set("decide", "running", message="Checking override rules + confidence matrix…")
        await asyncio.sleep(STAGE_PAUSE * 0.5)
        decision = approve.decide(incident, sensors, vision)
        workflow.tracker.set("decide", "done",
                             message=decision["reason"],
                             data=decision)
        events.emit("REASON", f"Decision: {decision['decision']} — {decision['reason']}")
        db.incidents().update_one({"_id": incident_id}, {"$set": {"decision": decision}})
        await asyncio.sleep(STAGE_PAUSE)

        # ── STAGE 6: EXECUTION ────────────────────────────────────────────
        if decision["decision"] == approve.AUTO_IMMEDIATE:
            workflow.tracker.set("execute", "running",
                                 message="Auto-approving immediately",
                                 data={"source": "auto_immediate",
                                       "steps_total": len(plan.get("steps", [])),
                                       "steps_done": 0,
                                       "steps": plan.get("steps", [])})
            events.emit("AUTO", "Auto-approving immediately", incident_id=incident_id)
            await execute_plan(incident_id, "auto_immediate")
        elif decision["decision"] == approve.AUTO_COUNTDOWN:
            workflow.tracker.set("execute", "waiting",
                                 message=f"Auto-approve in {decision['timeout_sec']}s — operator may override",
                                 data={"source": "auto_countdown",
                                       "timeout_sec": decision["timeout_sec"],
                                       "steps_total": len(plan.get("steps", [])),
                                       "steps_done": 0,
                                       "steps": plan.get("steps", [])})
            events.emit("AUTO", f"Countdown started — {decision['timeout_sec']}s to operator override", incident_id=incident_id)
            task = asyncio.create_task(approve.start_countdown(incident_id, decision["timeout_sec"], execute_plan))
            approve.register_task(incident_id, task)
        else:
            workflow.tracker.set("execute", "waiting",
                                 message=f"Awaiting operator decision · {decision['reason']}",
                                 data={"source": "hitl",
                                       "timeout_sec": decision["timeout_sec"],
                                       "steps_total": len(plan.get("steps", [])),
                                       "steps_done": 0,
                                       "steps": plan.get("steps", [])})
            events.emit("HITL", f"Human-in-the-loop required: {decision['reason']}", incident_id=incident_id)
    except Exception as e:
        events.emit("REASON", f"Pipeline error: {type(e).__name__}: {e}")
        workflow.tracker.set("rules", "failed", message=f"{type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
