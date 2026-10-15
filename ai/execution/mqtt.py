from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from .config import MqttConfig

log = logging.getLogger("execution")

STATUS_TOPIC = "homeai/status"
DISCOVERY = "homeassistant"
DEVICE = {"identifiers": ["homeai"], "name": "KI-Regeln", "manufacturer": "home-ai"}


@dataclass(frozen=True)
class Message:
    topic: str
    payload: str
    retain: bool = False


Handler = Callable[[Message], Awaitable[None]]
Callback = Callable[[], Awaitable[None]]


def topic_matches(topic_filter: str, topic: str) -> bool:
    filter_parts, topic_parts = topic_filter.split("/"), topic.split("/")
    for index, part in enumerate(filter_parts):
        if part == "#":
            return True
        if index >= len(topic_parts):
            return False
        if part != "+" and part != topic_parts[index]:
            return False
    return len(filter_parts) == len(topic_parts)


class InMemoryBus:
    """Bus für Tests: zeichnet Veröffentlichungen auf, Eingänge kommen über `deliver`."""

    def __init__(self) -> None:
        self.published: list[Message] = []
        self.retained: dict[str, str] = {}
        self._handlers: list[tuple[str, Handler]] = []
        self._connected: list[Callback] = []

    def on(self, topic_filter: str, handler: Handler) -> None:
        self._handlers.append((topic_filter, handler))

    def on_connect(self, callback: Callback) -> None:
        self._connected.append(callback)

    async def publish(self, topic: str, payload: str, *, retain: bool = False, qos: int = 1) -> None:
        self.published.append(Message(topic, payload, retain))
        if retain:
            if payload == "":
                self.retained.pop(topic, None)
            else:
                self.retained[topic] = payload

    def last(self, topic: str) -> Message | None:
        for message in reversed(self.published):
            if message.topic == topic:
                return message
        return None

    async def deliver(self, topic: str, payload: str, retain: bool = False) -> None:
        for topic_filter, handler in self._handlers:
            if topic_matches(topic_filter, topic):
                await handler(Message(topic, payload, retain))

    async def start(self) -> None:
        for callback in self._connected:
            await callback()

    async def run(self, stop: asyncio.Event) -> None:
        await self.start()
        await stop.wait()


class AioMqttBus:
    """Verbindung zum MQTT-Broker (aiomqtt). Wird nur im Dauerbetrieb und für Kommandos gebraucht."""

    def __init__(self, cfg: MqttConfig, identifier: str = "homeai-execution") -> None:
        self.cfg = cfg
        self.identifier = identifier
        self._handlers: list[tuple[str, Handler]] = []
        self._connected: list[Callback] = []
        self._client = None

    def on(self, topic_filter: str, handler: Handler) -> None:
        self._handlers.append((topic_filter, handler))

    def on_connect(self, callback: Callback) -> None:
        self._connected.append(callback)

    def _arguments(self, **extra) -> dict:
        return dict(hostname=self.cfg.host, port=self.cfg.port, username=self.cfg.user,
                    password=self.cfg.password, **extra)

    async def publish(self, topic: str, payload: str, *, retain: bool = False, qos: int = 1) -> None:
        if self._client is not None:
            await self._client.publish(topic, payload=payload, qos=qos, retain=retain)
            return
        import aiomqtt
        async with aiomqtt.Client(**self._arguments()) as client:      # kurze Verbindung für einzelne Kommandos
            await client.publish(topic, payload=payload, qos=qos, retain=retain)

    async def run(self, stop: asyncio.Event) -> None:
        import aiomqtt
        backoff = 1.0
        while not stop.is_set():
            try:
                will = aiomqtt.Will(topic=STATUS_TOPIC, payload="offline", qos=1, retain=True)
                async with aiomqtt.Client(**self._arguments(identifier=self.identifier, will=will)) as client:
                    self._client = client
                    backoff = 1.0
                    for topic_filter, _ in self._handlers:
                        await client.subscribe(topic_filter, qos=1)
                    await client.publish(STATUS_TOPIC, payload="online", qos=1, retain=True)
                    for callback in self._connected:
                        await callback()
                    reader = asyncio.create_task(self._read(client))
                    waiter = asyncio.create_task(stop.wait())
                    done, pending = await asyncio.wait({reader, waiter}, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    if reader in done:
                        reader.result()
                    else:
                        await client.publish(STATUS_TOPIC, payload="offline", qos=1, retain=True)
            except aiomqtt.MqttError as exc:
                log.warning("MQTT-Verbindung verloren: %s", exc)
            finally:
                self._client = None
            if stop.is_set():
                break
            try:
                await asyncio.wait_for(stop.wait(), backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 60.0)

    async def _read(self, client) -> None:
        async for message in client.messages:
            payload = message.payload
            text = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
            incoming = Message(str(message.topic), text, bool(message.retain))
            for topic_filter, handler in self._handlers:
                if topic_matches(topic_filter, incoming.topic):
                    try:
                        await handler(incoming)
                    except Exception:
                        log.exception("Fehler bei der Verarbeitung von %s", incoming.topic)


def _topic(component: str, rule_id: str, object_id: str) -> str:
    return f"{DISCOVERY}/{component}/homeai_{rule_id}/{object_id}/config"


def rule_discovery_topics(rule_id: str) -> list[str]:
    return [_topic("switch", rule_id, "aktiv"), _topic("sensor", rule_id, "kennzahlen"),
            _topic("sensor", rule_id, "trefferquote"), _topic("sensor", rule_id, "abweichung")]


def rule_entities(rule_id: str, label: str) -> list[tuple[str, dict]]:
    base = f"homeai/rule/{rule_id}"
    stats = f"{base}/stats"

    def sensor(object_id: str, name: str, template: str, **extra) -> tuple[str, dict]:
        return _topic("sensor", rule_id, object_id), {
            "name": f"{label} {name}", "unique_id": f"homeai_{rule_id}_{object_id}",
            "default_entity_id": f"sensor.homeai_{rule_id}_{object_id}", "state_topic": stats,
            "value_template": template, "device": DEVICE, **extra}

    return [
        # Der Schalter liegt in HA, merkt sich seinen Zustand selbst und braucht den Container nicht.
        (_topic("switch", rule_id, "aktiv"), {
            "name": f"{label} aktiv", "unique_id": f"homeai_{rule_id}_aktiv",
            "default_entity_id": f"switch.homeai_{rule_id}_aktiv", "command_topic": f"{base}/active/set",
            "payload_on": "ON", "payload_off": "OFF", "optimistic": True, "device": DEVICE}),
        sensor("kennzahlen", "Kennzahlen", "{{ value_json.reife }}", json_attributes_topic=stats),
        sensor("trefferquote", "Trefferquote",
               "{{ value_json.trefferquote_pct if value_json.trefferquote_pct is not none else 'unknown' }}",
               unit_of_measurement="%"),
        sensor("abweichung", "letzte Abweichung",
               "{{ value_json.abweichung if value_json.abweichung else 'keine' }}"),
    ]


def rule_label(rule_id: str, description: str) -> str:
    return f"Regel {rule_id} {description.split(':', 1)[0]}"


async def publish_rule_entities(bus, rule_id: str, label: str) -> None:
    for topic, payload in rule_entities(rule_id, label):
        await bus.publish(topic, json.dumps(payload, ensure_ascii=False), retain=True)


async def clear_rule_entities(bus, rule_id: str) -> None:
    for topic in rule_discovery_topics(rule_id):
        await bus.publish(topic, "", retain=True)
