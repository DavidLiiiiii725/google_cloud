from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


Severity = Literal["nominal", "warning", "critical", "catastrophic"]
Risk = Literal["low", "medium", "high"]


class SensorInput(BaseModel):
    """Partial sensor update from the frontend sliders."""
    temperature: float | None = None
    humidity: float | None = None
    co2: float | None = None
    par: float | None = None
    moisture_seedling: float | None = None
    moisture_growing: float | None = None
    moisture_harvest: float | None = None
    tank_level: float | None = None
    trigger_stress: bool = False


class ZoneSoil(BaseModel):
    moisture: float
    soil_temp: float
    ec: float


class ActuatorState(BaseModel):
    vent_pct: float = 0
    fan_rpm: float = 0
    irrigation: dict[str, bool] = Field(default_factory=lambda: {"seedling": False, "growing": False, "harvest": False})
    cooling: bool = False
    light_pct: float = 70
    co2_inject: bool = False


class TelemetryDoc(BaseModel):
    ts: float
    temperature: float
    humidity: float
    co2: float
    vpd: float
    par: float
    lux: float
    dli: float
    soil: dict[str, ZoneSoil]
    tank_level: float
    flow_rate: float
    ph: float
    solution_ec: float
    actuator: ActuatorState
    vision: dict | None = None


class ActionStep(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)
    rationale: str = ""


class ActionPlan(BaseModel):
    steps: list[ActionStep]
    risk: Risk
    summary: str
