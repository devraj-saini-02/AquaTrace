import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import SessionLocal
from app.models import Alert, ContaminantReadingRow, Edge, ModelVersionRow, Node, NodeStatus, SensorHealthEvent, SummaryWindow
from app.mqtt_service import MqttService
from app.mqtt_topics import (
    TOPIC_ALERT_SUB,
    TOPIC_MODEL_ACK_SUB,
    TOPIC_REGISTER_SUB,
    TOPIC_STATUS_SUB,
    TOPIC_SUMMARY_SUB,
    topic_register_ack,
)
from app.registry import get_registry
from app.scheduler import schedule_baselining_flip
from app.schemas import AlertPayload, ModelUpdateAck, RegistrationAck, RegistrationManifest, SummaryPayload
from app.topology import estimate_edge_geometry

logger = logging.getLogger(__name__)

# Filled in by app.tracing.engine at import time (phase 7) so an alert can kick off an
# on-demand trace without this module importing the tracing package (avoids a circular import:
# the tracing engine reads Alert/SummaryWindow rows, this module writes them).
on_alert_trigger_tracing = None


PARENT_WAIT_RETRIES = 8
PARENT_WAIT_DELAY_S = 0.5


async def _wait_for_parent(parent_id: str) -> bool:
    """Nodes register concurrently (orchestrator starts every Virtual Node's registration at
    once), so a child's manifest can arrive before its own parent has finished registering — a
    real fleet wouldn't guarantee power-on order either. Retry briefly instead of silently
    dropping the edge on a same-instant race. Polls in its own short-lived session rather than
    the caller's open transaction: that transaction stays uncommitted (and its connection checked
    out) for the whole registration handler, so sleeping between retries on it would hold a pool
    connection hostage for the entire wait -- under a full-fleet concurrent registration burst,
    that alone was enough to exhaust the pool before any handler got to do its actual work."""
    for attempt in range(PARENT_WAIT_RETRIES):
        async with SessionLocal() as probe:
            if await probe.get(Node, parent_id) is not None:
                return True
        if attempt < PARENT_WAIT_RETRIES - 1:
            await asyncio.sleep(PARENT_WAIT_DELAY_S)
    return False


async def resolve_node_id(session, manifest: RegistrationManifest) -> str:
    existing = await session.get(Node, manifest.proposed_node_id)
    if existing is None:
        return manifest.proposed_node_id
    same_unit = (
        abs(existing.latitude - manifest.latitude) < 1e-4
        and abs(existing.longitude - manifest.longitude) < 1e-4
    )
    if same_unit:
        return manifest.proposed_node_id
    suffix = 2
    while True:
        candidate = f"{manifest.proposed_node_id}-{suffix}"
        if await session.get(Node, candidate) is None:
            return candidate
        suffix += 1


async def _flip_to_active(node_id: str) -> None:
    async with SessionLocal() as session:
        node = await session.get(Node, node_id)
        if node is not None and node.status == NodeStatus.baselining:
            node.status = NodeStatus.active
            await session.commit()
            logger.info("node %s baselining complete -> active", node_id)


async def process_registration(manifest: RegistrationManifest) -> RegistrationAck:
    """Core registration logic, shared by the MQTT handler and the REST fallback
    (POST /register, §4.5) — the two are alternative transports for the same operation."""
    settings = get_settings()

    async with SessionLocal() as session:
        node_id = await resolve_node_id(session, manifest)
        node = await session.get(Node, node_id)
        if node is None:
            node = Node(
                node_id=node_id,
                latitude=manifest.latitude,
                longitude=manifest.longitude,
                breadth_m=manifest.breadth_m,
                invert_level_m=manifest.invert_level_m,
                status=NodeStatus.baselining,
            )
            session.add(node)
        else:
            node.latitude = manifest.latitude
            node.longitude = manifest.longitude
            node.breadth_m = manifest.breadth_m
            node.invert_level_m = manifest.invert_level_m
            node.status = NodeStatus.baselining
        await session.flush()

        assigned_parent_ids: list[str] = []
        for parent_id in manifest.claimed_parent_ids:
            if not await _wait_for_parent(parent_id):
                logger.warning("register %s claims unknown parent %s (gave up waiting); skipping edge", node_id, parent_id)
                continue
            parent = await session.get(Node, parent_id)
            existing_edge = (
                await session.execute(
                    select(Edge).where(Edge.parent_node_id == parent_id, Edge.child_node_id == node_id)
                )
            ).scalar_one_or_none()
            if existing_edge is None:
                length_m, manning_n, slope, tau_base_s = estimate_edge_geometry(
                    parent.latitude,
                    parent.longitude,
                    parent.invert_level_m,
                    node.latitude,
                    node.longitude,
                    node.invert_level_m,
                    node.breadth_m,
                )
                session.add(
                    Edge(
                        parent_node_id=parent_id,
                        child_node_id=node_id,
                        length_m=length_m,
                        manning_n=manning_n,
                        slope=slope,
                        tau_base_s=tau_base_s,
                        validated=False,
                    )
                )
            assigned_parent_ids.append(parent_id)

        registry = get_registry()
        baseline_model_refs: dict[str, str] = {}
        for sensor_id in manifest.sensor_ids:
            contaminant = registry.get(sensor_id)
            if contaminant is None or not contaminant.is_live_stream:
                continue
            latest = (
                await session.execute(
                    select(ModelVersionRow)
                    .where(ModelVersionRow.contaminant_id == sensor_id)
                    .order_by(ModelVersionRow.pushed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            baseline_model_refs[sensor_id] = latest.version if latest else f"{sensor_id}_baseline"

        await session.commit()

    ack = RegistrationAck(
        node_id=node_id,
        assigned_parent_ids=assigned_parent_ids,
        baseline_model_refs=baseline_model_refs,
        baselining_period_s=settings.baselining_period_seconds,
    )
    schedule_baselining_flip(node_id, settings.baselining_period_seconds, _flip_to_active)
    logger.info(
        "registered node %s (proposed=%s), parents=%s", node_id, manifest.proposed_node_id, assigned_parent_ids
    )
    return ack


async def handle_register(mqtt: MqttService, raw_payload: bytes) -> None:
    manifest = RegistrationManifest.model_validate_json(raw_payload)
    ack = await process_registration(manifest)
    await mqtt.publish(topic_register_ack(ack.node_id), ack, qos=1)


async def _track_sensor_health_transition(session, node_id: str, contaminant_id: str, health_state: str, at) -> None:
    """Opens/closes SensorHealthEvent rows as a node's reported sensor_health changes, so the
    dashboard's health/replacement queue (§10.2c) has real degradation history to show, not just
    a snapshot of the latest reading."""
    open_event = (
        await session.execute(
            select(SensorHealthEvent)
            .where(
                SensorHealthEvent.node_id == node_id,
                SensorHealthEvent.contaminant_id == contaminant_id,
                SensorHealthEvent.resolved_at.is_(None),
            )
            .order_by(SensorHealthEvent.detected_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if health_state == "OK":
        if open_event is not None:
            open_event.resolved_at = at
        return

    if open_event is not None:
        if open_event.health_state == health_state:
            return  # still the same degraded state, nothing new to record
        open_event.resolved_at = at  # state changed (e.g. SUSPECT -> FAILED); close the old one

    session.add(SensorHealthEvent(node_id=node_id, contaminant_id=contaminant_id, health_state=health_state, detected_at=at))


async def handle_summary(raw_payload: bytes) -> None:
    payload = SummaryPayload.model_validate_json(raw_payload)
    async with SessionLocal() as session:
        dup = (
            await session.execute(
                select(SummaryWindow.id).where(
                    SummaryWindow.node_id == payload.node_id, SummaryWindow.seq == payload.seq
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            logger.debug("duplicate summary node=%s seq=%s ignored", payload.node_id, payload.seq)
            return

        summary = SummaryWindow(
            node_id=payload.node_id,
            seq=payload.seq,
            t_start=payload.t_start,
            t_end=payload.t_end,
            level_m=payload.hydraulics.level_m,
            speed_mps=payload.hydraulics.speed_mps,
            q_m3s=payload.hydraulics.Q_m3s,
        )
        session.add(summary)
        await session.flush()
        for c in payload.contaminants:
            session.add(
                ContaminantReadingRow(
                    summary_id=summary.id,
                    contaminant_id=c.id,
                    mean=c.mean,
                    max=c.max,
                    median=c.median,
                    std=c.std,
                    flux_g_s=c.flux_g_s,
                    mass_g=c.mass_g,
                    t_max=c.t_max,
                    n=c.n,
                    sensor_health=c.sensor_health,
                    rule_state=c.rule_state,
                    ml_anomaly=c.ml_anomaly,
                    final_state=c.final_state,
                    model_ver=c.model_ver,
                )
            )
            await _track_sensor_health_transition(session, payload.node_id, c.id, c.sensor_health, payload.t_end)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()  # lost a race against a redelivered (node_id, seq) duplicate


async def handle_alert(raw_payload: bytes) -> None:
    payload = AlertPayload.model_validate_json(raw_payload)
    async with SessionLocal() as session:
        session.add(
            Alert(
                node_id=payload.node_id,
                contaminant_id=payload.contaminant_id,
                value=payload.value,
                state=payload.state,
                timestamp=payload.timestamp,
                trigger_type=payload.trigger_type,
            )
        )
        await session.commit()

    if on_alert_trigger_tracing is not None:
        await on_alert_trigger_tracing(payload.contaminant_id, payload.timestamp)


async def handle_status(node_id: str, raw_payload: bytes) -> None:
    status_str = raw_payload.decode("utf-8").strip().strip('"')
    async with SessionLocal() as session:
        node = await session.get(Node, node_id)
        if node is None:
            return
        if status_str == "offline":
            node.status = NodeStatus.offline
            await session.commit()
        elif status_str == "online" and node.status == NodeStatus.offline:
            node.status = NodeStatus.active
            await session.commit()


async def handle_model_ack(raw_payload: bytes) -> None:
    ack = ModelUpdateAck.model_validate_json(raw_payload)
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(ModelVersionRow)
                .where(
                    ModelVersionRow.node_id == ack.node_id,
                    ModelVersionRow.contaminant_id == ack.contaminant_id,
                    ModelVersionRow.version == ack.requested_version,
                )
                .order_by(ModelVersionRow.pushed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            logger.warning(
                "model ack for a push we have no record of: node=%s contaminant=%s version=%s",
                ack.node_id, ack.contaminant_id, ack.requested_version,
            )
            return

        row.acked_at = datetime.now(timezone.utc)
        row.running = ack.verified
        row.shadow_disagreement_rate = ack.shadow_disagreement_rate

        if ack.verified:
            # only one version should read as "running" per (node, contaminant) at a time
            others = (
                await session.execute(
                    select(ModelVersionRow).where(
                        ModelVersionRow.node_id == ack.node_id,
                        ModelVersionRow.contaminant_id == ack.contaminant_id,
                        ModelVersionRow.id != row.id,
                        ModelVersionRow.running.is_(True),
                    )
                )
            ).scalars().all()
            for other in others:
                other.running = False

        await session.commit()
    logger.info(
        "model update ack: node=%s contaminant=%s running=%s verified=%s disagreement=%s",
        ack.node_id, ack.contaminant_id, ack.running_version, ack.verified, ack.shadow_disagreement_rate,
    )


def build_ingestion_service() -> MqttService:
    settings = get_settings()

    async def dispatch(topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        if len(parts) < 3 or parts[0] != "nodes":
            return
        node_id, kind = parts[1], parts[2]
        if kind == "register":
            await handle_register(service, payload)
        elif kind == "summary":
            await handle_summary(payload)
        elif kind == "alert":
            await handle_alert(payload)
        elif kind == "status":
            await handle_status(node_id, payload)
        elif kind == "model" and len(parts) >= 4 and parts[3] == "ack":
            await handle_model_ack(payload)

    subscriptions = [
        (TOPIC_REGISTER_SUB, 1),
        (TOPIC_SUMMARY_SUB, 1),
        (TOPIC_ALERT_SUB, 2),
        (TOPIC_STATUS_SUB, 1),
        (TOPIC_MODEL_ACK_SUB, 2),
    ]
    service = MqttService(settings, subscriptions, dispatch)
    return service
