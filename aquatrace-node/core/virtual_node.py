"""Virtual Node (§5.1b): one instance per simulated physical node. Pulls readings from the
World Simulator each tick, computes hydraulics-derived flux/mass, classifies against registry
thresholds (ML anomaly scoring is wired in during the training phase — see core/virtual_node.py
git history / README for the phased build order), runs sensor-health gating and alerting, and
publishes summaries/alerts over MQTT exactly as an independently-deployed physical device would.
"""
import asyncio
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from config import Settings
from core.alerting import AlertingTracker
from core.health import ContaminantHealthTracker
from ml.inference import SHADOW_DISAGREEMENT_THRESHOLD, LoadedModel, ModelStore
from protocol.mqtt_client import NodeMqttClient
from protocol.mqtt_topics import topic_alert, topic_model_ack, topic_summary
from protocol.schemas import (
    AlertPayload,
    ContaminantReading,
    HydraulicsBlock,
    ModelUpdateAck,
    ModelUpdateCommand,
    NodeHealth,
    RegistrationManifest,
    SummaryPayload,
)
from registry import Registry
from simulator.clock import SimClock
from simulator.world import WorldSimulator

logger = logging.getLogger(__name__)

SAMPLE_INTERVAL_S = 30.0
SUMMARY_CYCLE_S = 720.0  # 12 simulated minutes, within the spec's 10-15 minute range

_HEALTH_ORDER = ("OK", "SUSPECT", "FAILED")
_STATE_ORDER = ("SAFE", "WARN", "RED")


@dataclass
class ShadowEvaluation:
    candidate: LoadedModel
    cmd: ModelUpdateCommand
    deadline: datetime
    disagreements: int = 0
    total: int = 0


def _worst(order: tuple, values: list[str]) -> str:
    rank = {v: i for i, v in enumerate(order)}
    return max(values, key=lambda v: rank[v])


class VirtualNode:
    def __init__(
        self,
        world: WorldSimulator,
        clock: SimClock,
        registry: Registry,
        settings: Settings,
        proposed_node_id: str,
        model_store: Optional[ModelStore] = None,
    ):
        self.world = world
        self.clock = clock
        self.registry = registry
        self.settings = settings
        self.proposed_node_id = proposed_node_id
        self.node_id = proposed_node_id
        self.sensor_ids = world.sensor_ids_for(proposed_node_id)
        self.model_store = model_store or ModelStore(settings)

        self.mqtt = NodeMqttClient(settings, proposed_node_id, self._handle_model_update)
        self.health_trackers = {c: ContaminantHealthTracker(c) for c in self.sensor_ids}
        self.alerting_trackers = {c: AlertingTracker() for c in self.sensor_ids}

        self.model_versions: dict[str, str] = {}
        self.parent_ids: list[str] = []
        self.baselining_period_s = 0
        self.registered_at: Optional[datetime] = None

        self._cycle_records: dict[str, list[dict]] = {c: [] for c in self.sensor_ids}
        self._cycle_start: Optional[datetime] = None
        self._seq = 0
        self._stopped = False
        self._latest_hydraulics = (0.0, 0.0, 0.0)
        self._task: Optional[asyncio.Task] = None
        self._shadow: dict[str, ShadowEvaluation] = {}

    # ---- lifecycle ----

    async def run(self) -> None:
        mqtt_task = asyncio.create_task(self.mqtt.run())
        try:
            await self.mqtt.wait_connected()
            ack = await self.mqtt.register(self._build_manifest())
            self.node_id = ack.node_id
            self.parent_ids = ack.assigned_parent_ids
            self.model_versions = dict(ack.baseline_model_refs)
            self.baselining_period_s = ack.baselining_period_s
            self.registered_at = self.clock.now()
            logger.info("virtual node %s registered, parents=%s", self.node_id, self.parent_ids)
            await self._tick_loop()
        finally:
            mqtt_task.cancel()

    async def stop(self) -> None:
        self._stopped = True
        await self.mqtt.stop()

    def _build_manifest(self) -> RegistrationManifest:
        spec = self.world.node_spec(self.proposed_node_id)
        return RegistrationManifest(
            proposed_node_id=self.proposed_node_id,
            sensor_ids=self.sensor_ids,
            breadth_m=spec.breadth_m,
            invert_level_m=spec.invert_level_m,
            latitude=spec.latitude,
            longitude=spec.longitude,
            claimed_parent_ids=self.world.claimed_parent_ids(self.proposed_node_id),
        )

    # ---- tick loop ----

    async def _tick_loop(self) -> None:
        self._cycle_start = self.clock.now()
        real_sleep = SAMPLE_INTERVAL_S / self.settings.sim_time_scale
        while not self._stopped:
            await asyncio.sleep(real_sleep)
            await self._sample_tick(self.clock.now())

    async def _sample_tick(self, t: datetime) -> None:
        level, v, q = self.world.get_hydraulics(self.node_id, t)
        self._latest_hydraulics = (level, v, q)

        for contaminant_id in self.sensor_ids:
            raw = self.world.get_reading(self.node_id, contaminant_id, t)
            health = self.health_trackers[contaminant_id].evaluate(raw)
            contam = self.registry[contaminant_id]
            rule_state = contam.classify(raw)
            features = self._features(contaminant_id, raw, level, v, q)
            ml_state, ml_anomaly = self.model_store.predict(contaminant_id, features)
            final_state = _worst(_STATE_ORDER, [rule_state, ml_state])

            self._cycle_records[contaminant_id].append(
                {
                    "value": raw,
                    "health": health,
                    "rule_state": rule_state,
                    "final_state": final_state,
                    "ml_anomaly": ml_anomaly,
                    "t": t,
                }
            )

            shadow = self._shadow.get(contaminant_id)
            if shadow is not None:
                candidate_state, _candidate_score = shadow.candidate.predict(features)
                shadow.total += 1
                if candidate_state != ml_state:
                    shadow.disagreements += 1
                if t >= shadow.deadline:
                    await self._finalize_shadow(contaminant_id)

            alert = self.alerting_trackers[contaminant_id].update(contam, raw, rule_state)
            if alert is not None:
                state, trigger_type = alert
                await self._emit_alert(contaminant_id, raw, state, trigger_type, t)

        if (t - self._cycle_start).total_seconds() >= SUMMARY_CYCLE_S:
            await self._emit_summary(self._cycle_start, t)
            self._cycle_start = t
            self._cycle_records = {c: [] for c in self.sensor_ids}
            self._seq += 1

    def _features(self, contaminant_id: str, raw: float, level: float, v: float, q: float) -> dict:
        recent = [r["value"] for r in self._cycle_records[contaminant_id][-20:]]
        rolling_mean = statistics.fmean(recent) if recent else raw
        rolling_std = statistics.pstdev(recent) if len(recent) > 1 else 0.0
        return {"value": raw, "level_m": level, "speed_mps": v, "Q_m3s": q, "rolling_mean_1h": rolling_mean, "rolling_std_1h": rolling_std}

    # ---- emission ----

    async def _emit_alert(self, contaminant_id: str, value: float, state: str, trigger_type: str, t: datetime) -> None:
        payload = AlertPayload(
            node_id=self.node_id, contaminant_id=contaminant_id, value=value, state=state, timestamp=t, trigger_type=trigger_type
        )
        await self.mqtt.publish(topic_alert(self.node_id, contaminant_id), payload, qos=2)

    async def _emit_summary(self, t_start: datetime, t_end: datetime) -> None:
        level, v, q = self._latest_hydraulics
        readings = [
            self._aggregate(contaminant_id, records, q, t_start, t_end)
            for contaminant_id, records in self._cycle_records.items()
            if records
        ]
        payload = SummaryPayload(
            node_id=self.node_id,
            seq=self._seq,
            t_start=t_start,
            t_end=t_end,
            hydraulics=HydraulicsBlock(level_m=level, speed_mps=v, Q_m3s=q),
            contaminants=readings,
            node_health=NodeHealth(batt=self._battery_level(t_end), rssi=-55, buffer_depth=0),
        )
        await self.mqtt.publish(topic_summary(self.node_id), payload, qos=1)

    def _running_model_version(self, contaminant_id: str) -> str:
        """What's actually loaded and producing predictions right now, not what CC's
        registration ack claimed at boot -- a node can bootstrap a locally-bundled model (see
        ml/inference.py ModelStore._bootstrap_from_disk) before ever hearing from CC, exactly
        as a real device ships with a pretrained model already on board. self.model_versions
        only reflects OTA history (baseline_model_refs at registration, then each verified
        ModelUpdateCommand); it can go stale the moment a local model loads independently."""
        loaded = self.model_store.active.get(contaminant_id)
        if loaded is not None:
            return loaded.version
        return self.model_versions.get(contaminant_id, f"{contaminant_id}_baseline")

    def _aggregate(self, contaminant_id: str, records: list[dict], q: float, t_start: datetime, t_end: datetime) -> ContaminantReading:
        values = [r["value"] for r in records]
        mean_v = statistics.fmean(values)
        max_v = max(values)
        median_v = statistics.median(values)
        std_v = statistics.pstdev(values) if len(values) > 1 else 0.0
        n = len(values)
        t_max = next(r["t"] for r in records if r["value"] == max_v)
        health = _worst(_HEALTH_ORDER, [r["health"] for r in records])
        rule_state = _worst(_STATE_ORDER, [r["rule_state"] for r in records])
        final_state = _worst(_STATE_ORDER, [r["final_state"] for r in records])
        ml_scores = [r["ml_anomaly"] for r in records if r["ml_anomaly"] is not None]
        ml_anomaly = max(ml_scores) if ml_scores else None

        flux_g_s = None
        mass_g = None
        if contaminant_id not in ("ph", "temperature", "conductivity"):
            # conductivity is used as a travel-time tracer via its concentration signal shape,
            # not mass balance (uS/cm has no mass equivalent); ph/temperature are intrinsic
            # water-body properties, not transported pollutant mass (see simulator/world.py)
            flux_g_s = mean_v * q
            mass_g = flux_g_s * (t_end - t_start).total_seconds()

        return ContaminantReading(
            id=contaminant_id,
            mean=mean_v,
            max=max_v,
            median=median_v,
            std=std_v,
            flux_g_s=flux_g_s,
            mass_g=mass_g,
            t_max=t_max,
            n=n,
            sensor_health=health,
            rule_state=rule_state,
            ml_anomaly=ml_anomaly,
            final_state=final_state,
            model_ver=self._running_model_version(contaminant_id),
        )

    def _battery_level(self, t: datetime) -> float:
        elapsed_h = (t - (self.registered_at or t)).total_seconds() / 3600.0
        return max(4.2 - 0.01 * elapsed_h, 3.0)

    # ---- OTA (§5.1d / §6) ----

    async def _handle_model_update(self, cmd: ModelUpdateCommand) -> None:
        candidate = await self.model_store.download_and_load(cmd)
        if candidate is None:
            await self._publish_model_ack(cmd.contaminant_id, cmd.model_version, verified=False, shadow_rate=None)
            return

        if not cmd.shadow_mode:
            self.model_store.set_active(cmd.contaminant_id, candidate)
            self.model_versions[cmd.contaminant_id] = cmd.model_version
            await self._publish_model_ack(cmd.contaminant_id, cmd.model_version, verified=True, shadow_rate=None)
            return

        self._shadow[cmd.contaminant_id] = ShadowEvaluation(
            candidate=candidate, cmd=cmd, deadline=self.clock.now() + timedelta(seconds=cmd.shadow_duration_s)
        )
        logger.info(
            "node %s: shadow eval started for %s %s, deciding in %ss",
            self.node_id, cmd.contaminant_id, cmd.model_version, cmd.shadow_duration_s,
        )

    async def _finalize_shadow(self, contaminant_id: str) -> None:
        shadow = self._shadow.pop(contaminant_id)
        rate = shadow.disagreements / shadow.total if shadow.total else 0.0
        verified = rate <= SHADOW_DISAGREEMENT_THRESHOLD
        if verified:
            self.model_store.set_active(contaminant_id, shadow.candidate)
            self.model_versions[contaminant_id] = shadow.cmd.model_version
        logger.info(
            "node %s: shadow eval finished for %s %s: n=%s disagreement_rate=%.3f verified=%s",
            self.node_id, contaminant_id, shadow.cmd.model_version, shadow.total, rate, verified,
        )
        await self._publish_model_ack(contaminant_id, shadow.cmd.model_version, verified=verified, shadow_rate=rate)

    async def _publish_model_ack(self, contaminant_id: str, requested_version: str, verified: bool, shadow_rate: Optional[float]) -> None:
        ack = ModelUpdateAck(
            node_id=self.node_id,
            contaminant_id=contaminant_id,
            requested_version=requested_version,
            running_version=self._running_model_version(contaminant_id),
            verified=verified,
            shadow_disagreement_rate=shadow_rate,
        )
        await self.mqtt.publish(topic_model_ack(self.node_id), ack, qos=2)
