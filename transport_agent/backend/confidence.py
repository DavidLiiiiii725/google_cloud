"""Confidence scoring — 5 weighted components, range 0.0–1.0."""
from __future__ import annotations
from . import db, state

WEIGHTS = {
    "deviation": 0.30,
    "agreement": 0.20,
    "trend": 0.15,
    "vision": 0.20,
    "history": 0.15,
}


def score(assessment: dict, vision: dict | None = None) -> dict:
    anomalies = assessment.get("anomalies", [])
    if not anomalies:
        return {"score": 0.0, "components": {k: 0.0 for k in WEIGHTS}}

    # 1. Deviation magnitude — max |z| normalized to 4.0
    max_z = max(abs(a["z"]) for a in anomalies)
    deviation = min(1.0, max_z / 4.0)

    # 2. Sensor agreement — fraction of independent sensors flagged
    flagged_groups = {a["sensor"].split("_")[0] for a in anomalies}
    agreement = min(1.0, len(flagged_groups) / 4.0)

    # 3. Trend duration — count of recent telemetry docs with same severity
    severity = assessment["severity"]
    recent = db.live_telemetry().find(sort=[("ts", -1)], limit=5)
    same = sum(1 for d in recent if d.get("severity") == severity)
    trend = min(1.0, same / 3.0)

    # 4. Vision confirmation
    if vision:
        vc = vision.get("severity_match", 0.0)
        vision_score = float(vc)
    else:
        vision_score = 0.0

    # 5. Historical incident rate — similar prior incidents in `incidents`
    prior = db.incidents().count_documents({"severity": severity})
    history = min(1.0, prior / 5.0)

    components = {
        "deviation": deviation, "agreement": agreement, "trend": trend,
        "vision": vision_score, "history": history,
    }
    raw = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)
    # If vision absent, redistribute its weight proportionally
    if not vision:
        raw = raw / (1.0 - WEIGHTS["vision"])
    return {"score": round(min(1.0, raw), 3), "components": components}
