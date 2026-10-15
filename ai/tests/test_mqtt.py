import json

import pytest

from execution.mqtt import (InMemoryBus, Message, clear_rule_entities, publish_rule_entities, rule_discovery_topics,
                            rule_entities, rule_label, topic_matches)


@pytest.mark.parametrize("flt,topic,expected", [
    ("homeai/rule/+/fired", "homeai/rule/r5/fired", True),
    ("homeai/rule/+/fired", "homeai/rule/r5/stats", False),
    ("homeai/rule/+/active/set", "homeai/rule/r5/active/set", True),
    ("homeai/cmd/+", "homeai/cmd/scan", True),
    ("homeai/cmd/+", "homeai/cmd/scan/extra", False),
    ("homeai/#", "homeai/a/b/c", True),
    ("homeai/rule/+", "homeai/rule", False),
])
def test_topic_matching(flt, topic, expected):
    assert topic_matches(flt, topic) is expected


async def test_in_memory_bus_records_retains_and_delivers():
    bus = InMemoryBus()
    seen = []

    async def handler(message: Message):
        seen.append(message)

    bus.on("homeai/cmd/+", handler)
    await bus.publish("homeai/status", "online", retain=True)
    await bus.publish("homeai/status", "", retain=True)
    await bus.publish("homeai/x", "1")
    assert bus.retained == {} and [m.topic for m in bus.published] == ["homeai/status", "homeai/status", "homeai/x"]
    assert bus.last("homeai/x").payload == "1" and bus.last("homeai/none") is None
    await bus.deliver("homeai/cmd/scan", "PRESS")
    await bus.deliver("homeai/other", "x")
    assert [(m.topic, m.payload) for m in seen] == [("homeai/cmd/scan", "PRESS")]


async def test_connect_callbacks_run_on_start():
    bus = InMemoryBus()
    calls = []

    async def callback():
        calls.append("connected")

    bus.on_connect(callback)
    await bus.start()
    assert calls == ["connected"]


def test_rule_entities_follow_the_spec():
    entities = dict(rule_entities("r5", "Bad Mo-Fr 06:30"))
    switch = entities["homeassistant/switch/homeai_r5/aktiv/config"]
    assert switch["default_entity_id"] == "switch.homeai_r5_aktiv"
    assert switch["command_topic"] == "homeai/rule/r5/active/set"
    assert switch["optimistic"] is True and switch["payload_on"] == "ON"
    assert "availability_topic" not in switch and "state_topic" not in switch     # gilt auch ohne Container
    sensors = {payload["default_entity_id"]: payload for topic, payload in entities.items() if "/sensor/" in topic}
    assert set(sensors) == {"sensor.homeai_r5_kennzahlen", "sensor.homeai_r5_trefferquote", "sensor.homeai_r5_abweichung"}
    for payload in sensors.values():
        assert payload["state_topic"] == "homeai/rule/r5/stats"
        assert payload["unique_id"].startswith("homeai_r5_")
    assert sensors["sensor.homeai_r5_kennzahlen"]["json_attributes_topic"] == "homeai/rule/r5/stats"
    assert sensors["sensor.homeai_r5_trefferquote"]["unit_of_measurement"] == "%"


def test_rule_label_uses_the_place_before_the_first_colon():
    assert rule_label("r5", "Bad: Mo-Fr 06:30 -> Preset comfort") == "Regel r5 Bad"


async def test_publish_and_clear_rule_entities():
    bus = InMemoryBus()
    await publish_rule_entities(bus, "r5", "Bad")
    assert set(bus.retained) == set(rule_discovery_topics("r5"))
    assert all(json.loads(payload)["unique_id"] for payload in bus.retained.values())
    await clear_rule_entities(bus, "r5")
    assert bus.retained == {}
