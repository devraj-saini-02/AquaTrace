"""Wire (MQTT) Pydantic models — protocol spec §2.2, implemented identically (field-for-field)
in node/protocol/schemas.py. The two files are duplicated on purpose, not imported from a
shared package: node/ and central-system/ are independent services that only agree on shape
because both sides implement the same spec, exactly as two independently-deployed real systems
would. If you change one, change the other."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class HydraulicsBlock(BaseModel):
    level_m: float
    speed_mps: float
    Q_m3s: float


class ContaminantReading(BaseModel):
    id: str
    mean: float
    max: float
    median: float
    std: Optional[float] = None
    flux_g_s: Optional[float] = None
    mass_g: Optional[float] = None
    t_max: Optional[datetime] = None
    n: int
    sensor_health: Literal["OK", "SUSPECT", "FAILED"]
    rule_state: Literal["SAFE", "WARN", "RED"]
    ml_anomaly: Optional[float] = None
    final_state: Literal["SAFE", "WARN", "RED"]
    model_ver: str


class NodeHealth(BaseModel):
    batt: Optional[float] = None
    rssi: Optional[int] = None
    buffer_depth: int


class SummaryPayload(BaseModel):
    node_id: str
    seq: int
    t_start: datetime
    t_end: datetime
    hydraulics: HydraulicsBlock
    contaminants: list[ContaminantReading]
    node_health: NodeHealth


class AlertPayload(BaseModel):
    node_id: str
    contaminant_id: str
    value: float
    state: Literal["WARN", "RED"]
    timestamp: datetime
    trigger_type: Literal["fast_path", "debounced", "cusum"]


class RegistrationManifest(BaseModel):
    proposed_node_id: str
    sensor_ids: list[str]
    breadth_m: float
    invert_level_m: Optional[float] = None
    latitude: float
    longitude: float
    claimed_parent_ids: list[str] = []


class RegistrationAck(BaseModel):
    node_id: str
    assigned_parent_ids: list[str]
    baseline_model_refs: dict[str, str]
    baselining_period_s: int


class ModelUpdateCommand(BaseModel):
    node_id: str
    contaminant_id: str
    model_version: str
    model_path: str
    checksum_sha256: str
    shadow_mode: bool = True
    shadow_duration_s: int = 300


class ModelUpdateAck(BaseModel):
    node_id: str
    contaminant_id: str
    requested_version: str
    running_version: str
    verified: bool
    shadow_disagreement_rate: Optional[float] = None
