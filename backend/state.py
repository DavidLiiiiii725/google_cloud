"""In-process mutable state — current sensor snapshot + actuator state.
MongoDB is the audit/history log; this is the live working set."""
from __future__ import annotations
import math
import threading
import time
from .models import ActuatorState

_lock = threading.Lock()

# Baseline starting values
_sensors = {
    "temperature": 24.0,
    "humidity": 65.0,
    "co2": 800.0,
    "par": 450.0,
    "moisture_seedling": 60.0,
    "moisture_growing": 55.0,
    "moisture_harvest": 50.0,
    "tank_level": 78.0,
    "soil_temp_seedling": 22.0,
    "soil_temp_growing": 22.5,
    "soil_temp_harvest": 23.0,
    "ec_seedling": 1.6,
    "ec_growing": 1.8,
    "ec_harvest": 2.0,
    "flow_rate": 0.0,
    "ph": 6.2,
    "solution_ec": 1.8,
}

_actuator = ActuatorState()
_zone_health = {"seedling": 1.0, "growing": 1.0, "harvest": 1.0}
_dli_accum = 0.0
_dli_last_ts = time.time()
_current_severity = "nominal"
_current_incident_id: str | None = None
_confidence_boosts: dict[str, tuple[float, float]] = {}  # tool -> (boost, expires_ts)


def get_sensors() -> dict:
    with _lock:
        return dict(_sensors)


def update_sensors(patch: dict) -> dict:
    with _lock:
        for k, v in patch.items():
            if v is None:
                continue
            if k in _sensors:
                _sensors[k] = float(v)
        return dict(_sensors)


def get_actuator() -> ActuatorState:
    with _lock:
        return _actuator.model_copy(deep=True)


def update_actuator(**kw) -> ActuatorState:
    with _lock:
        for k, v in kw.items():
            if hasattr(_actuator, k):
                if k == "irrigation" and isinstance(v, dict):
                    _actuator.irrigation.update(v)
                else:
                    setattr(_actuator, k, v)
        return _actuator.model_copy(deep=True)


def get_zone_health() -> dict:
    with _lock:
        return dict(_zone_health)


def set_zone_health(zh: dict):
    with _lock:
        _zone_health.update(zh)


def get_severity() -> str:
    return _current_severity


def set_severity(s: str):
    global _current_severity
    _current_severity = s


def get_incident_id() -> str | None:
    return _current_incident_id


def set_incident_id(iid: str | None):
    global _current_incident_id
    _current_incident_id = iid


def vpd_kpa(temp_c: float, rh_pct: float) -> float:
    """Vapour pressure deficit in kPa from temperature (°C) and relative humidity (%)."""
    es = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    ea = es * (rh_pct / 100.0)
    return round(max(0.0, es - ea), 3)


def lux_from_par(par: float) -> float:
    return round(par * 54.0, 1)  # rough conversion for sunlight-equivalent


def tick_dli(par: float) -> float:
    """Accumulate daily light integral (mol/m²/day) — resets if last update was >1h ago (proxy for new day)."""
    global _dli_accum, _dli_last_ts
    now = time.time()
    dt = now - _dli_last_ts
    if dt > 3600:  # treat as fresh day
        _dli_accum = 0.0
    _dli_accum += par * dt * 1e-6  # µmol/m²/s × seconds = µmol/m²; ×1e-6 → mol/m²
    _dli_last_ts = now
    return round(_dli_accum, 3)


def bump_confidence_boost(tool: str, amount: float = 0.10, ttl_sec: float = 86400):
    _confidence_boosts[tool] = (amount, time.time() + ttl_sec)


def confidence_boost_for(tool: str) -> float:
    rec = _confidence_boosts.get(tool)
    if not rec:
        return 0.0
    amount, expires = rec
    if time.time() > expires:
        _confidence_boosts.pop(tool, None)
        return 0.0
    return amount


def reset():
    """Hard reset to demo baseline."""
    global _actuator, _dli_accum, _dli_last_ts, _current_severity, _current_incident_id
    with _lock:
        _sensors.update({
            "temperature": 24.0, "humidity": 65.0, "co2": 800.0, "par": 450.0,
            "moisture_seedling": 60.0, "moisture_growing": 55.0, "moisture_harvest": 50.0,
            "tank_level": 78.0,
            "soil_temp_seedling": 22.0, "soil_temp_growing": 22.5, "soil_temp_harvest": 23.0,
            "ec_seedling": 1.6, "ec_growing": 1.8, "ec_harvest": 2.0,
            "flow_rate": 0.0, "ph": 6.2, "solution_ec": 1.8,
        })
        _actuator = ActuatorState()
        _zone_health.update({"seedling": 1.0, "growing": 1.0, "harvest": 1.0})
        _dli_accum = 0.0
        _dli_last_ts = time.time()
        _current_severity = "nominal"
        _current_incident_id = None
