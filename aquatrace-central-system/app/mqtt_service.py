import asyncio
import logging
import ssl
from typing import Awaitable, Callable, Optional

import asyncio_mqtt as aiomqtt
from pydantic import BaseModel

from app.config import Settings

logger = logging.getLogger(__name__)

RECONNECT_DELAY_S = 3
MessageHandler = Callable[[str, bytes], Awaitable[None]]


class MqttService:
    """Persistent MQTT connection with reconnect-on-drop. Subscribes to the fixed set of
    inbound topics (protocol §2.1) and dispatches each message to `on_message`; also exposes
    `publish()` for the same connection so REST handlers (e.g. POST /models/push) and the
    ingestion handlers share one client rather than opening a second connection."""

    def __init__(self, settings: Settings, subscriptions: list[tuple[str, int]], on_message: MessageHandler):
        self._settings = settings
        self._subscriptions = subscriptions
        self._on_message = on_message
        self._client: Optional[aiomqtt.Client] = None
        self._stopped = False
        self._inflight: set[asyncio.Task] = set()

    async def run(self) -> None:
        while not self._stopped:
            try:
                async with aiomqtt.Client(
                    hostname=self._settings.mqtt_broker_host,
                    port=self._settings.mqtt_broker_port,
                    username=self._settings.mqtt_username or None,
                    password=self._settings.mqtt_password or None,
                    client_id="central-system-cc",
                    tls_context=ssl.create_default_context() if self._settings.mqtt_use_tls else None,
                ) as client:
                    self._client = client
                    for topic, qos in self._subscriptions:
                        await client.subscribe(topic, qos=qos)
                    logger.info("MQTT connected, subscribed to %s", [t for t, _ in self._subscriptions])
                    async with client.messages() as messages:
                        async for message in messages:
                            # Dispatch each message as its own task rather than awaiting
                            # handlers sequentially. A handler that legitimately waits on
                            # something else arriving over MQTT (e.g. a child's registration
                            # retrying while its parent's own registration is still in flight —
                            # see app/mqtt_ingestion.py _wait_for_parent) would otherwise starve
                            # this very loop of the message it's waiting for: classic
                            # self-inflicted head-of-line blocking.
                            task = asyncio.create_task(self._run_handler(str(message.topic), message.payload))
                            self._inflight.add(task)
                            task.add_done_callback(self._inflight.discard)
            except aiomqtt.MqttError as e:
                self._client = None
                logger.warning("MQTT connection lost (%s), reconnecting in %ss", e, RECONNECT_DELAY_S)
                await asyncio.sleep(RECONNECT_DELAY_S)

    async def _run_handler(self, topic: str, payload: bytes) -> None:
        try:
            await self._on_message(topic, payload)
        except Exception:
            logger.exception("error handling message on %s", topic)

    def stop(self) -> None:
        self._stopped = True
        for task in self._inflight:
            task.cancel()

    async def publish(self, topic: str, payload: BaseModel | str | bytes, qos: int = 1, retain: bool = False) -> None:
        if self._client is None:
            raise RuntimeError("MQTT client not connected yet")
        body = payload.model_dump_json() if isinstance(payload, BaseModel) else payload
        await self._client.publish(topic, body, qos=qos, retain=retain)
