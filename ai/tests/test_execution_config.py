from pathlib import Path

import pytest

from execution.config import ExecutionConfigError, load_execution_config

ROOT = Path(__file__).resolve().parent.parent
ENV = {"HA_URL": "ws://ha.test:8123/api/websocket", "HA_WRITE_TOKEN": "tok", "MQTT_HOST": "broker.test",
       "MQTT_USER": "u", "MQTT_PASSWORD": "p"}


def test_shipped_config_loads_with_defaults():
    cfg = load_execution_config(ROOT / "config" / "execution.yaml", ENV)
    assert cfg.ha_url == "ws://ha.test:8123/api/websocket" and cfg.require_token() == "tok"
    assert cfg.mqtt.host == "broker.test" and cfg.mqtt.port == 1883 and cfg.mqtt.user == "u"
    assert cfg.min_distance_min == 60 and cfg.blocked_states == ("Vacation",)
    assert (cfg.setpoint_min, cfg.setpoint_max) == (15.0, 25.0)
    assert (cfg.eval_before_min, cfg.eval_after_min, cfg.target_tolerance) == (60, 90, 0.5)
    assert (cfg.min_scored, cfg.max_fail_rate, cfg.llm_runs_per_day) == (10, 0.2, 3)
    assert cfg.blueprint_path == "fabsss/ki_regel.yaml" and cfg.dashboard_path == "ki-regeln"
    rooms = {room.key: room for room in cfg.rooms.values()}
    assert set(rooms) == {"bad", "schlafzimmer", "wohnzimmer", "bastelzimmer"}
    assert rooms["bad"].heating_automation == "automation.heating_control_bathroom"
    assert cfg.rooms["office"].key == "bastelzimmer"


def test_mqtt_is_optional_and_token_required_on_demand():
    cfg = load_execution_config(ROOT / "config" / "execution.yaml", {"HA_URL": "ws://x/api/websocket"})
    assert cfg.mqtt is None
    with pytest.raises(ExecutionConfigError, match="HA_WRITE_TOKEN"):
        cfg.require_token()


def test_missing_url_and_bad_rooms_are_rejected(tmp_path):
    with pytest.raises(ExecutionConfigError, match="HA_URL"):
        load_execution_config(ROOT / "config" / "execution.yaml", {})
    broken = tmp_path / "x.yaml"
    broken.write_text("rooms:\n  a: {key: bad}\n", encoding="utf-8")
    with pytest.raises(ExecutionConfigError, match="heating_automation"):
        load_execution_config(broken, ENV)
    twice = tmp_path / "y.yaml"
    twice.write_text("rooms:\n  a: {key: bad, heating_automation: automation.a}\n"
                     "  b: {key: bad, heating_automation: automation.b}\n", encoding="utf-8")
    with pytest.raises(ExecutionConfigError, match="doppelt"):
        load_execution_config(twice, ENV)
