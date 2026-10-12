"""Round-trip test: serialize a wire model -> publish over the real local Mosquitto broker ->
subscribe -> deserialize, and confirm the object survives the trip unchanged. Requires a
broker reachable at MQTT_BROKER_HOST:MQTT_BROKER_PORT (see central-system/README or
docker-compose.yml)."""
import asyncio
from datetime import datetime, timezone

import asyncio_mqtt as aiomqtt
import pytest

from app.config import get_settings
from app.schemas import (
    AlertPayload,
    ContaminantReading,
    HydraulicsBlock,
    ModelUpdateAck,
    ModelUpdateCommand,
    NodeHealth,
    RegistrationAck,
    RegistrationManifest,
    SummaryPayload,
)


async def _roundtrip(topic: str, model, qos: int = 1):
    settings = get_settings()
    async with aiomqtt.Client(
        settings.mqtt_broker_host, settings.mqtt_broker_port, client_id="pytest-roundtrip"
    ) as client:
        await client.subscribe(topic, qos=qos)
        async with client.messages() as messages:
            await client.publish(topic, model.model_dump_json(), qos=qos)
            async for message in messages:
                return type(model).model_validate_json(message.payload)


@pytest.mark.asyncio
async def test_summary_payload_roundtrip():
    now = datetime.now(timezone.utc)
    original = SummaryPayload(
        node_id="TEST01",
        seq=42,
        t_start=now,
        t_end=now,
        hydraulics=HydraulicsBlock(level_m=0.4, speed_mps=0.6, Q_m3s=0.72),
        contaminants=[
            ContaminantReading(
                id="ph", mean=7.2, max=7.5, median=7.2, std=0.1, n=60,
                sensor_health="OK", rule_state="SAFE", final_state="SAFE", model_ver="ph_v1",
            )
        ],
        node_health=NodeHealth(batt=3.9, rssi=-55, buffer_depth=0),
    )
    received = await asyncio.wait_for(_roundtrip("test/roundtrip/summary", original), timeout=5)
    assert received == original


@pytest.mark.asyncio
async def test_alert_payload_roundtrip():
    original = AlertPayload(
        node_id="TEST01", contaminant_id="tss", value=150.0, state="RED",
        timestamp=datetime.now(timezone.utc), trigger_type="fast_path",
    )
    received = await asyncio.wait_for(_roundtrip("test/roundtrip/alert", original, qos=2), timeout=5)
    assert received == original


@pytest.mark.asyncio
async def test_registration_manifest_and_ack_roundtrip():
    manifest = RegistrationManifest(
        proposed_node_id="TEST01", sensor_ids=["ph", "conductivity"], breadth_m=3.0,
        invert_level_m=200.0, latitude=28.6, longitude=77.2, claimed_parent_ids=["TEST00"],
    )
    received_manifest = await asyncio.wait_for(_roundtrip("test/roundtrip/register", manifest), timeout=5)
    assert received_manifest == manifest

    ack = RegistrationAck(
        node_id="TEST01", assigned_parent_ids=["TEST00"],
        baseline_model_refs={"ph": "ph_baseline"}, baselining_period_s=90,
    )
    received_ack = await asyncio.wait_for(_roundtrip("test/roundtrip/ack", ack), timeout=5)
    assert received_ack == ack


@pytest.mark.asyncio
async def test_model_update_command_and_ack_roundtrip():
    cmd = ModelUpdateCommand(
        node_id="TEST01", contaminant_id="tss", model_version="tss_v2",
        model_path="/models/tss_v2.pkl", checksum_sha256="a" * 64, shadow_mode=True, shadow_duration_s=300,
    )
    received_cmd = await asyncio.wait_for(_roundtrip("test/roundtrip/model_cmd", cmd, qos=2), timeout=5)
    assert received_cmd == cmd

    ack = ModelUpdateAck(
        node_id="TEST01", contaminant_id="tss", requested_version="tss_v2",
        running_version="tss_v2", verified=True, shadow_disagreement_rate=0.02,
    )
    received_ack = await asyncio.wait_for(_roundtrip("test/roundtrip/model_ack", ack, qos=2), timeout=5)
    assert received_ack == ack
