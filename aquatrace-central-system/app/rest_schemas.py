"""REST-only DTOs — distinct from app/schemas.py (the MQTT wire protocol models). These are
central-system's own API contract with dashboard/; nothing here is duplicated into node/."""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel


class NodeOut(BaseModel):
    node_id: str
    latitude: float
    longitude: float
    breadth_m: float
    invert_level_m: Optional[float]
    status: str
    registered_at: datetime


class ContaminantReadingOut(BaseModel):
    contaminant_id: str
    mean: float
    max: float
    median: float
    std: Optional[float]
    flux_g_s: Optional[float]
    mass_g: Optional[float]
    t_max: Optional[datetime]
    n: int
    sensor_health: str
    rule_state: str
    ml_anomaly: Optional[float]
    final_state: str
    model_ver: str


class LatestSummaryOut(BaseModel):
    seq: int
    t_start: datetime
    t_end: datetime
    level_m: float
    speed_mps: float
    q_m3s: float
    received_at: datetime
    contaminants: list[ContaminantReadingOut]


class NodeDetailOut(NodeOut):
    parent_ids: list[str]
    child_ids: list[str]
    latest_summary: Optional[LatestSummaryOut]


class EdgeOut(BaseModel):
    id: str
    parent_node_id: str
    child_node_id: str
    length_m: float
    manning_n: float
    slope: float
    tau_base_s: float
    validated: bool


class IncidentOut(BaseModel):
    id: str
    status: str
    contaminant_id: str
    created_at: datetime
    updated_at: datetime
    affected_node_count: int
    top_confidence: Optional[float]
    candidate_causes: list[dict[str, Any]]


class IncidentDetailOut(IncidentOut):
    node_ids: list[str]


class NodeTimeseriesPoint(BaseModel):
    t: datetime
    node_id: str
    concentration: float
    mass_g: float
    flux_g_s: float
    final_state: str


class EdgeTimeseriesPoint(BaseModel):
    t: datetime
    parent_id: str
    child_id: str
    flux_g_s: float


class IncidentTimeseriesOut(BaseModel):
    incident_id: str
    t_start: datetime
    t_end: datetime
    nodes: list[NodeTimeseriesPoint]
    edges: list[EdgeTimeseriesPoint]


class ModelPushRequest(BaseModel):
    node_id: str
    contaminant_id: str
    model_path: str
    model_version: str


class ModelVersionOut(BaseModel):
    id: str
    node_id: str
    contaminant_id: str
    version: str
    pushed_at: datetime
    acked_at: Optional[datetime]
    running: bool
    shadow_disagreement_rate: Optional[float]


class SensorHealthEventOut(BaseModel):
    id: str
    node_id: str
    contaminant_id: str
    health_state: str
    detected_at: datetime
    resolved_at: Optional[datetime]
