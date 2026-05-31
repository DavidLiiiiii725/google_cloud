"""Gemini wrapper using google-genai with Vertex AI + ADC.
Always degrades gracefully: if Vertex is unavailable, returns cached responses keyed by severity."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
from typing import Any
from . import config, events

_client = None
_init_failed = False


def _get_client():
    global _client, _init_failed
    if _client is not None:
        return _client
    if _init_failed:
        return None
    try:
        from google import genai
        _client = genai.Client(
            vertexai=True,
            project=config.GCP_PROJECT,
            location=config.GCP_REGION,
        )
        return _client
    except Exception as e:
        _init_failed = True
        print(f"[gemini] init failed: {e} — will use cached fallbacks")
        return None


# ---- Cached fallback responses ---------------------------------------------

VISION_FALLBACKS = {
    "warning": {
        "severity_confirmed": "warning",
        "severity_match": 0.7,
        "affected_zones": ["growing", "harvest"],
        "root_cause": "Elevated leaf temperature and mild wilting in the harvest zone — early heat stress likely from rising air temperature combined with low humidity.",
        "raw": "Plants in zone 2 show slight downward leaf orientation. Foliage colour remains green but turgor is reduced. Recommend ventilation increase and zone 2 irrigation.",
    },
    "critical": {
        "severity_confirmed": "critical",
        "severity_match": 0.92,
        "affected_zones": ["harvest", "growing"],
        "root_cause": "Acute heat + low-humidity stress (high VPD). Harvest zone leaves visibly drooping with early curl; growing zone showing onset of stress. Immediate cooling and irrigation required.",
        "raw": "Visible leaf curl on harvest plants. Stomatal closure inferred from posture. VPD pressure consistent with > 2.0 kPa. Risk of fruit abortion within 30 minutes.",
    },
    "catastrophic": {
        "severity_confirmed": "catastrophic",
        "severity_match": 0.97,
        "affected_zones": ["seedling", "growing", "harvest"],
        "root_cause": "Severe whole-greenhouse heat stress. All zones showing wilting; harvest zone plants beginning to slump. Emergency cooling and irrigation needed across all zones.",
        "raw": "Generalised wilting across all three zones. Harvest plants slumped sideways. Soil surface dry. Imminent crop loss.",
    },
    "nominal": {
        "severity_confirmed": "nominal",
        "severity_match": 0.0,
        "affected_zones": [],
        "root_cause": "All zones healthy. Foliage upright, colour uniform.",
        "raw": "Nominal.",
    },
}


def _vision_prompt(snapshot: dict) -> str:
    return (
        "You are a greenhouse monitoring assistant. Three images show the SEEDLING, GROWING, "
        "and HARVEST zones in that order. Compare them against the sensor snapshot below and "
        "decide:\n"
        "  1. severity_confirmed: one of nominal | warning | critical | catastrophic\n"
        "  2. severity_match: 0.0-1.0 — how confident you are that the visual evidence matches the sensor alarm\n"
        "  3. affected_zones: list any of [seedling, growing, harvest]\n"
        "  4. root_cause: one short sentence in plain English\n"
        "Respond ONLY as compact JSON with these four keys.\n\n"
        f"Sensor snapshot: {json.dumps(snapshot, default=str)}"
    )


def vision_assess(snapshot: dict, image_paths: list[Path], expected_severity: str) -> dict:
    """Multimodal call — send sensor snapshot + 3 zone images to Gemini."""
    client = _get_client()
    if client is None:
        events.emit("REASON", "Vision unavailable — using cached assessment for severity tier")
        return dict(VISION_FALLBACKS.get(expected_severity, VISION_FALLBACKS["warning"]))

    try:
        from google.genai import types as gtypes
        parts: list[Any] = [_vision_prompt(snapshot)]
        for p in image_paths:
            if p.exists():
                data = p.read_bytes()
                parts.append(gtypes.Part.from_bytes(data=data, mime_type="image/png"))
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=parts,
            config=gtypes.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
        raw = (resp.text or "").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # try to salvage by trimming code fences
            cleaned = raw.strip("`").lstrip("json").strip()
            parsed = json.loads(cleaned)
        parsed.setdefault("raw", raw[:400])
        return parsed
    except Exception as e:
        events.emit("REASON", f"Vision call failed ({type(e).__name__}) — using cached fallback")
        return dict(VISION_FALLBACKS.get(expected_severity, VISION_FALLBACKS["warning"]))


# ---- Action planning via tool use ------------------------------------------

ACTION_TOOLS = [
    {"name": "open_vents", "args": {"percent": "0-100"}, "risk": "low"},
    {"name": "set_fan_speed", "args": {"rpm": "0-3000"}, "risk": "low"},
    {"name": "activate_irrigation", "args": {"zone": "seedling|growing|harvest", "minutes": "1-15"}, "risk": "medium"},
    {"name": "activate_cooling", "args": {}, "risk": "medium"},
    {"name": "adjust_lighting", "args": {"percent": "0-100"}, "risk": "low"},
    {"name": "inject_co2", "args": {"ppm_target": "400-1500"}, "risk": "medium"},
    {"name": "schedule_recheck", "args": {"minutes": "1-120"}, "risk": "low"},
]

PLAN_FALLBACKS = {
    "warning": [
        {"tool": "open_vents", "args": {"percent": 40}, "rationale": "Lower air temperature passively before active cooling"},
        {"tool": "set_fan_speed", "args": {"rpm": 1200}, "rationale": "Increase air circulation to reduce VPD"},
        {"tool": "schedule_recheck", "args": {"minutes": 20}, "rationale": "Confirm passive measures sufficient"},
    ],
    "critical": [
        {"tool": "open_vents", "args": {"percent": 80}, "rationale": "Maximise passive cooling immediately"},
        {"tool": "activate_cooling", "args": {}, "rationale": "Active cooling required — passive alone insufficient at this VPD"},
        {"tool": "set_fan_speed", "args": {"rpm": 2200}, "rationale": "Force circulation across all zones"},
        {"tool": "activate_irrigation", "args": {"zone": "harvest", "minutes": 4}, "rationale": "Harvest zone most stressed — restore turgor"},
        {"tool": "activate_irrigation", "args": {"zone": "growing", "minutes": 3}, "rationale": "Growing zone showing early stress"},
        {"tool": "schedule_recheck", "args": {"minutes": 10}, "rationale": "Verify recovery within one cycle"},
    ],
    "catastrophic": [
        {"tool": "open_vents", "args": {"percent": 100}, "rationale": "Full vent opening — emergency"},
        {"tool": "activate_cooling", "args": {}, "rationale": "Emergency cooling"},
        {"tool": "set_fan_speed", "args": {"rpm": 3000}, "rationale": "Max airflow"},
        {"tool": "activate_irrigation", "args": {"zone": "harvest", "minutes": 6}, "rationale": "Save harvest first"},
        {"tool": "activate_irrigation", "args": {"zone": "growing", "minutes": 5}, "rationale": "Growing zone rescue"},
        {"tool": "activate_irrigation", "args": {"zone": "seedling", "minutes": 4}, "rationale": "Preventative for seedlings"},
        {"tool": "adjust_lighting", "args": {"percent": 40}, "rationale": "Reduce radiant load"},
        {"tool": "schedule_recheck", "args": {"minutes": 5}, "rationale": "Tight follow-up"},
    ],
}


def _plan_prompt(snapshot: dict, vision: dict, severity: str) -> str:
    tools_desc = "\n".join(
        f"- {t['name']}({', '.join(f'{k}={v}' for k,v in t['args'].items())}) [risk={t['risk']}]"
        for t in ACTION_TOOLS
    )
    return (
        "You are the action-planning agent for an automated greenhouse. Produce a step-by-step mitigation "
        "plan using ONLY these tools. Order steps by urgency. Output JSON with this shape:\n"
        '  {"steps":[{"tool":"name","args":{...},"rationale":"..."}], "risk":"low|medium", "summary":"one short sentence"}\n\n'
        f"Severity: {severity}\n"
        f"Sensors: {json.dumps(snapshot, default=str)}\n"
        f"Vision finding: {json.dumps(vision, default=str)}\n\n"
        f"Available tools:\n{tools_desc}\n"
    )


def action_plan(snapshot: dict, vision: dict, severity: str) -> dict:
    client = _get_client()
    if client is None or severity == "nominal":
        steps = PLAN_FALLBACKS.get(severity, PLAN_FALLBACKS["warning"])
        risk = _classify_risk(steps)
        return {"steps": steps, "risk": risk, "summary": f"Cached {severity}-tier plan"}

    try:
        from google.genai import types as gtypes
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=_plan_prompt(snapshot, vision, severity),
            config=gtypes.GenerateContentConfig(
                temperature=0.3,
                response_mime_type="application/json",
            ),
        )
        raw = (resp.text or "").strip()
        plan = json.loads(raw)
        plan.setdefault("risk", _classify_risk(plan.get("steps", [])))
        plan.setdefault("summary", f"{severity} mitigation plan")
        return plan
    except Exception as e:
        events.emit("REASON", f"Action plan call failed ({type(e).__name__}) — using cached plan")
        steps = PLAN_FALLBACKS.get(severity, PLAN_FALLBACKS["warning"])
        return {"steps": steps, "risk": _classify_risk(steps), "summary": f"Cached {severity} plan"}


def _classify_risk(steps: list[dict]) -> str:
    medium = {"activate_irrigation", "activate_cooling", "inject_co2"}
    for s in steps:
        if s.get("tool") in medium:
            return "medium"
    return "low"
