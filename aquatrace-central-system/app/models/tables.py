import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class NodeStatus(str, enum.Enum):
    baselining = "baselining"
    active = "active"
    offline = "offline"


class IncidentStatus(str, enum.Enum):
    NEW = "NEW"
    ONGOING = "ONGOING"
    ESCALATING = "ESCALATING"
    RESOLVED = "RESOLVED"


class Node(Base):
    __tablename__ = "nodes"

    node_id: Mapped[str] = mapped_column(String, primary_key=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    breadth_m: Mapped[float] = mapped_column(Float, nullable=False)
    invert_level_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[NodeStatus] = mapped_column(
        String, default=NodeStatus.baselining, nullable=False
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Edge(Base):
    __tablename__ = "edges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_node_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"), nullable=False)
    child_node_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"), nullable=False)
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    manning_n: Mapped[float] = mapped_column(Float, nullable=False)
    slope: Mapped[float] = mapped_column(Float, nullable=False)
    tau_base_s: Mapped[float] = mapped_column(Float, nullable=False)
    validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (UniqueConstraint("parent_node_id", "child_node_id", name="uq_edge_parent_child"),)


class SummaryWindow(Base):
    __tablename__ = "summary_windows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    t_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    t_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    level_m: Mapped[float] = mapped_column(Float, nullable=False)
    speed_mps: Mapped[float] = mapped_column(Float, nullable=False)
    q_m3s: Mapped[float] = mapped_column(Float, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    readings: Mapped[list["ContaminantReadingRow"]] = relationship(
        back_populates="summary", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("node_id", "seq", name="uq_summary_node_seq"),
        Index("ix_summary_node_tend", "node_id", "t_end"),
    )


class ContaminantReadingRow(Base):
    __tablename__ = "contaminant_readings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    summary_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("summary_windows.id", ondelete="CASCADE"), nullable=False
    )
    contaminant_id: Mapped[str] = mapped_column(String, nullable=False)
    mean: Mapped[float] = mapped_column(Float, nullable=False)
    max: Mapped[float] = mapped_column(Float, nullable=False)
    median: Mapped[float] = mapped_column(Float, nullable=False)
    std: Mapped[float | None] = mapped_column(Float, nullable=True)
    flux_g_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    mass_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    t_max: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    sensor_health: Mapped[str] = mapped_column(String, nullable=False)
    rule_state: Mapped[str] = mapped_column(String, nullable=False)
    ml_anomaly: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_state: Mapped[str] = mapped_column(String, nullable=False)
    model_ver: Mapped[str] = mapped_column(String, nullable=False)

    summary: Mapped["SummaryWindow"] = relationship(back_populates="readings")

    __table_args__ = (Index("ix_reading_contaminant", "contaminant_id"),)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"), nullable=False)
    contaminant_id: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[IncidentStatus] = mapped_column(String, default=IncidentStatus.NEW, nullable=False)
    contaminant_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    candidate_causes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # The tracing run's actual [t_start, t_end] (simulated time — see app/tracing/engine.py),
    # widened on each merge (app/mqtt_ingestion.py record_incident_candidate). Used by
    # GET /incidents/{id}/timeseries (§4.5) to know what window to query — central-system has no
    # clock of its own, so this can't be derived from created_at/updated_at (real wall-clock).
    window_t_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_t_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    nodes: Mapped[list["IncidentNode"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )


class IncidentNode(Base):
    __tablename__ = "incident_nodes"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"), primary_key=True)

    incident: Mapped["Incident"] = relationship(back_populates="nodes")


class ModelVersionRow(Base):
    __tablename__ = "model_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"), nullable=False)
    contaminant_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    pushed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    running: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    shadow_disagreement_rate: Mapped[float | None] = mapped_column(Float, nullable=True)


class SensorHealthEvent(Base):
    __tablename__ = "sensor_health_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"), nullable=False)
    contaminant_id: Mapped[str] = mapped_column(String, nullable=False)
    health_state: Mapped[str] = mapped_column(String, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
