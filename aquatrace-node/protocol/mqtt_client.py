"""Node-side MQTT wrapper: publishes register/summary/alert/status, subscribes to model-update
commands, and provides a one-shot register() call that publishes the manifest and waits for the
ack. From central-system's point of view this is indistinguishable from an independently-deployed
physical device talking the same wire protocol."""
import asyncio
import logging
import ssl
from typing import Awaitable, Callable, Optional

import asyncio_mqtt as aiomqtt
from pydantic import BaseModel

from config import Settings
from protocol.mqtt_topics import topic_model_update, topic_register, topic_register_ack, topic_status
from protocol.schemas import ModelUpdateCommand, RegistrationAck, RegistrationManifest

logger = logging.getLogger(__name__)

RECONNECT_DELAY_S = 3
REGISTER_TIMEOUT_S = 10.0
REGISTER_RETRIES = 5

ModelUpdateHandler = Callable[[ModelUpdateCommand], Awaitable[None]]


class NodeMqttClient:
    def __init__(self, settings: Settings, node_id: str, on_model_update: ModelUpdateHandler):
        # `node_id` here is the *proposed* id, used for subscriptions from connect time onward
        # — a real device doesn't know in advance whether CC will rename it on collision,
        # and neither does this client; if CC does assign a different id (a rare edge case for
        # this simulator's deterministic ids), status tracking under the proposed id's topic is
        # the accepted simplification, documented in node/README.
        self._settings = settings
        self.node_id = node_id
        self._on_model_update = on_model_update
        self._client: Optional[aiomqtt.Client] = None
        self._connected = asyncio.Event()
        self._ack_future: Optional[asyncio.Future] = None
        self._stopped = False

    async def wait_connected(self) -> None:
        await self._connected.wait()

    async def run(self) -> None:
        while not self._stopped:
            try:
                async with aiomqtt.Client(
                    hostname=self._settings.mqtt_broker_host,
                    port=self._settings.mqtt_broker_port,
                    username=self._settings.mqtt_username or None,
                    password=self._settings.mqtt_password or None,
                    client_id=f"node-{self.node_id}",
                    tls_context=ssl.create_default_context() if self._settings.mqtt_use_tls else None,
                    # Default is 60s. Many VirtualNode tick loops share one event loop and do
                    # synchronous per-contaminant ML inference with no internal await point; on a
                    # CPU-limited host a burst of ticks can stall the loop long enough to miss a
                    # PINGREQ, and the broker drops the connection for a missed keepalive. Wider
                    # margin here costs nothing and avoids that false-positive disconnect.
                    keepalive=180,
                ) as client:
                    self._client = client
                    await client.publish(topic_status(self.node_id), b"online", qos=1, retain=True)
                    await client.subscribe(topic_register_ack(self.node_id), qos=1)
                    await client.subscribe(topic_model_update(self.node_id), qos=2)
                    self._connected.set()
                    logger.info("node %s MQTT connected", self.node_id)
                    async with client.messages() as messages:
                        async for message in messages:
                            try:
                                await self._dispatch(str(message.topic), message.payload)
                            except Exception:
                                logger.exception("node %s: error handling message on %s", self.node_id, message.topic)
            except aiomqtt.MqttError as e:
                self._client = None
                self._connected.clear()
                logger.warning("node %s MQTT connection lost (%s), reconnecting in %ss", self.node_id, e, RECONNECT_DELAY_S)
                await asyncio.sleep(RECONNECT_DELAY_S)

    async def stop(self) -> None:
        # Not a broker-registered Last Will: the MQTT credential this deployment uses (HiveMQ
        # Cloud free tier) refuses any CONNECT that registers a Will at all, regardless of
        # topic/QoS/retain (confirmed empirically, not documented by HiveMQ). Publishing
        # "offline" explicitly here covers the graceful-stop path; an ungraceful crash or
        # network drop won't be detected by central-system the way a real LWT would catch it.
        self._stopped = True
        if self._client is not None:
            try:
                await self._client.publish(topic_status(self.node_id), b"offline", qos=1, retain=True)
            except aiomqtt.MqttError:
                pass

    async def _dispatch(self, topic: str, payload: bytes) -> None:
        if topic == topic_register_ack(self.node_id):
            if self._ack_future is not None and not self._ack_future.done():
                self._ack_future.set_result(RegistrationAck.model_validate_json(payload))
        elif topic == topic_model_update(self.node_id):
            await self._on_model_update(ModelUpdateCommand.model_validate_json(payload))

    async def publish(self, topic: str, payload: BaseModel | str | bytes, qos: int = 1, retain: bool = False) -> None:
        await self.wait_connected()
        body = payload.model_dump_json() if isinstance(payload, BaseModel) else payload
        await self._client.publish(topic, body, qos=qos, retain=retain)

    async def register(self, manifest: RegistrationManifest) -> RegistrationAck:
        await self.wait_connected()
        loop = asyncio.get_event_loop()
        for attempt in range(1, REGISTER_RETRIES + 1):
            self._ack_future = loop.create_future()
            await self.publish(topic_register(self.node_id), manifest, qos=1)
            try:
                return await asyncio.wait_for(self._ack_future, timeout=REGISTER_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning("node %s: registration ack timeout (attempt %s/%s)", self.node_id, attempt, REGISTER_RETRIES)
        raise TimeoutError(f"node {self.node_id}: no registration ack after {REGISTER_RETRIES} attempts")
