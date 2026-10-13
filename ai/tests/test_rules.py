from dataclasses import replace

import pytest

from collector.config import ConfigError
from collector.models import EntityInfo
from collector.rules import resolve_rule


def test_better_thermostat_is_heating_climate(cfg):
    rule = resolve_rule(EntityInfo("climate.better_thermostat_office", None, "buero"), cfg)
    assert (rule.kind, rule.domain) == ("climate", "heating")
    assert rule.attrs == (
        "temperature", "preset_mode", "hvac_action",
        "current_temperature", "current_humidity", "window_open",
    )
    assert rule.change_attrs == ("temperature", "preset_mode", "window_open")
    assert rule.delta_attrs == {"current_temperature": 0.5}


def test_temperature_sensor_uses_device_class_policy(cfg):
    rule = resolve_rule(EntityInfo("sensor.environment_sensor_outside_temperature", "temperature"), cfg)
    assert (rule.kind, rule.domain) == ("env", "shared")
    assert rule.numeric_state_delta == 0.5
    assert rule.heartbeat_s == 1800


def test_humidity_sensor_threshold_is_five(cfg):
    rule = resolve_rule(EntityInfo("sensor.environment_sensor_outside_humidity", "humidity"), cfg)
    assert rule.numeric_state_delta == 5.0


def test_presence_binary_sensor_kind_comes_from_device_class(cfg):
    rule = resolve_rule(EntityInfo("binary_sensor.presence_office_presence", "occupancy", "buero"), cfg)
    assert (rule.kind, rule.domain) == ("presence", "shared")
    assert rule.numeric_state_delta is None


def test_exception_overrides_domain_of_heating_switch(cfg):
    rule = resolve_rule(EntityInfo("input_boolean.heating_on_off"), cfg)
    assert (rule.kind, rule.domain) == ("flat", "heating")


def test_area_sensor_gets_debounce_from_exception(cfg):
    rule = resolve_rule(EntityInfo("sensor.fabians_s22_presence_tracker_area_last_seen"), cfg)
    assert rule.kind == "presence"
    assert rule.debounce_s == 60


def test_weather_has_attribute_deltas(cfg):
    rule = resolve_rule(EntityInfo("weather.home"), cfg)
    assert rule.kind == "weather"
    assert rule.delta_attrs == {"temperature": 0.5, "humidity": 5.0, "wind_speed": 5.0}


def test_unknown_domain_falls_back_to_shared_flat(cfg):
    rule = resolve_rule(EntityInfo("foo.bar"), cfg)
    assert (rule.kind, rule.domain) == ("flat", "shared")


def test_override_is_applied_below_config_exceptions(cfg):
    rule = resolve_rule(EntityInfo("climate.trv_office"), cfg, override={"kind": "climate", "domain": "heating"})
    assert (rule.kind, rule.domain) == ("climate", "heating")
    assert rule.attrs[0] == "temperature"


def test_change_attrs_must_be_whitelisted(cfg):
    bad = replace(cfg, exceptions={"climate.x": {"attrs": ["temperature"], "change_attrs": ["preset_mode"]}})
    with pytest.raises(ConfigError, match="preset_mode"):
        resolve_rule(EntityInfo("climate.x"), bad)
