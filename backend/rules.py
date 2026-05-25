"""Rules engine: z-scores vs baseline, anomaly flagging, per-zone health.
No LLM — fast deterministic pre-filter."""
from __future__ import annotations
import datetime as dt
from . import state, seed, config

# How quickly each zone responds to environmental stress (multiplier on damage rate)
ZONE_SENSITIVITY = {"seedling": 0.7, "growing": 1.0, "harvest": 1.3}


def _zscore(value: float, mean: float, std: float) -> float:
    if std <= 0:
        return 0.0
    return (value - mean) / std


def _severity_for_z(z: float, in_warn: bool, in_crit: bool) -> str:
    if in_crit:
        return "critical" if abs(z) < 4 else "catastrophic"
    if in_warn:
        return "warning"
    return "nominal"


def assess(sensors: dict) -> dict:
    """Return {severity, anomalies: [...], zone_health, zone_health_delta, vpd, dli, lux}."""
    month = dt.datetime.now().month
    baseline = seed.get_baseline(month)

    # Derived
    vpd = state.vpd_kpa(sensors["temperature"], sensors["humidity"])
    lux = state.lux_from_par(sensors["par"])
    dli = state.tick_dli(sensors["par"])

    anomalies: list[dict] = []

    def check(name: str, value: float, baseline_key: str | None = None):
        bk = baseline_key or name
        b = baseline.get(bk)
        if not b:
            return
        in_warn = value < b["warn_low"] or value > b["warn_high"]
        in_crit = value < b["crit_low"] or value > b["crit_high"]
        z = _zscore(value, b["mean"], b["std"])
        sev = _severity_for_z(z, in_warn, in_crit)
        if sev != "nominal":
            anomalies.append({
                "sensor": name, "value": round(value, 2), "z": round(z, 2),
                "severity": sev, "baseline_mean": b["mean"],
            })

    check("temperature", sensors["temperature"])
    check("humidity", sensors["humidity"])
    check("co2", sensors["co2"])
    check("vpd", vpd)
    check("par", sensors["par"])
    check("tank_level", sensors["tank_level"])
    for zone in config.ZONES:
        check(f"moisture_{zone}", sensors[f"moisture_{zone}"], baseline_key="moisture")

    # Per-zone health update — degrade or recover based on local stress + active mitigation
    cur = state.get_zone_health()
    actuator = state.get_actuator()
    new_health = {}
    for zone in config.ZONES:
        stress = 0.0
        # Air stress affects all zones equally
        for a in anomalies:
            if a["sensor"] in ("temperature", "humidity", "vpd", "co2"):
                stress += 0.10 if a["severity"] == "warning" else 0.20 if a["severity"] == "critical" else 0.30
            if a["sensor"] == f"moisture_{zone}":
                stress += 0.15 if a["severity"] == "warning" else 0.30 if a["severity"] == "critical" else 0.45
        stress *= ZONE_SENSITIVITY[zone]

        # Active mitigation: damp stress and add recovery even during anomalies
        active = 0
        if actuator.cooling:                     active += 1
        if actuator.vent_pct > 50:               active += 1
        if actuator.fan_rpm > 1000:              active += 1
        if actuator.irrigation.get(zone):        active += 1
        if active >= 2:   stress *= 0.35   # heavy mitigation
        elif active >= 1: stress *= 0.65

        recovery = 0.0
        if not anomalies:                                recovery += 0.10
        if actuator.irrigation.get(zone):                recovery += 0.05
        if actuator.cooling:                             recovery += 0.04
        if actuator.vent_pct > 50 and sensors["temperature"] < 32: recovery += 0.02

        delta = recovery - stress
        new_health[zone] = max(0.0, min(1.0, cur[zone] + delta))
    state.set_zone_health(new_health)

    # Aggregate severity = worst of anomalies
    severity_order = ["nominal", "warning", "critical", "catastrophic"]
    sev = "nominal"
    for a in anomalies:
        if severity_order.index(a["severity"]) > severity_order.index(sev):
            sev = a["severity"]
    state.set_severity(sev)

    return {
        "severity": sev,
        "anomalies": anomalies,
        "zone_health": new_health,
        "vpd": vpd,
        "lux": lux,
        "dli": dli,
    }
