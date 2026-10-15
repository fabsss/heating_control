import json
from datetime import timedelta
from pathlib import Path

import pytest

from tests.ha_template import HaEnv, minutes_ago, utc
from tests.yaml_blueprint import load_blueprint, resolve

BLUEPRINT = Path(__file__).resolve().parents[2] / "blueprints" / "ki_regel.yaml"
NOW = utc(2026, 10, 15, 6, 30)

INPUTS = dict(
    rule_id="r5", heating_entity="climate.bt", room_toggle="input_boolean.room", rule_toggle="switch.rule",
    stop_toggle="input_boolean.stop", flat_state="input_select.flat", blocked_states=["Vacation"],
    winter_toggle=[], heating_automation="automation.hc", last_intervention="input_datetime.li",
    min_distance_min=60, setpoint_min=15, setpoint_max=25, delay_min=0, action_kind="preset",
    action_preset="comfort", action_setpoint=21, rule_triggers=[], rule_conditions=[],
)


def decide(*, states=None, attrs=None, **inputs):
    blueprint = load_blueprint(BLUEPRINT)
    values = {**INPUTS, **inputs}
    base_states = {"input_boolean.room": "on", "switch.rule": "on", "input_boolean.stop": "off",
                   "input_select.flat": "Home", "climate.bt": "heat"}
    base_attrs = {"climate.bt": {"preset_mode": "eco", "temperature": 18.0},
                  "input_datetime.li": {"timestamp": minutes_ago(NOW, 180)},
                  "automation.hc": {"current": 0, "last_triggered": None}}
    env = HaEnv({**base_states, **(states or {})}, {**base_attrs, **(attrs or {})}, NOW)
    context = resolve(blueprint["variables"], values)
    step = next(step for step in blueprint["actions"] if step.get("alias") == "Entscheidung")
    result = env.render_variables(resolve(step["variables"], values), context)
    payload = result["payload"]
    result["payload"] = json.loads(payload) if isinstance(payload, str) else payload
    return result


def test_rule_acts_when_everything_is_on():
    result = decide()
    assert result["reason"] == "" and result["will_act"] is True and result["mode"] == "aktiv"
    assert result["payload"]["result"] == "ausgefuehrt"
    assert result["payload"]["target"] == {"kind": "preset", "value": "comfort"}
    assert result["payload"]["current"] == {"preset": "eco", "temperature": 18.0, "hvac_mode": "heat"}
    assert result["payload"]["room"] == "climate.bt" and result["payload"]["rule_id"] == "r5"


@pytest.mark.parametrize("states,reason,mode", [
    ({"switch.rule": "off"}, "schatten", "schatten"),
    ({"input_boolean.stop": "on"}, "schatten", "schatten"),
    ({"input_boolean.room": "off"}, "raum_aus", "schatten"),
])
def test_switches_decide_between_shadow_and_active(states, reason, mode):
    result = decide(states=states)
    assert (result["reason"], result["mode"], result["will_act"]) == (reason, mode, False)
    assert result["payload"]["result"] == "nicht_ausgefuehrt"


def test_unset_rule_switch_means_shadow():
    assert decide(states={"switch.rule": "unknown"})["reason"] == "schatten"


@pytest.mark.parametrize("flat", ["Home", "Away", "Sleep", "Coming Home", "Guest Mode"])
def test_only_vacation_is_blocked_by_default(flat):
    assert decide(states={"input_select.flat": flat})["reason"] == ""


def test_vacation_and_winter_mode_off_block_the_rule():
    assert decide(states={"input_select.flat": "Vacation"})["reason"] == "wohnungszustand"
    result = decide(winter_toggle="input_boolean.winter", states={"input_boolean.winter": "off"})
    assert result["reason"] == "wohnungszustand"
    assert decide(winter_toggle="input_boolean.winter", states={"input_boolean.winter": "on"})["reason"] == ""


def test_blocked_states_are_configurable():
    assert decide(blocked_states=["Vacation", "Sleep"], states={"input_select.flat": "Sleep"})["reason"] == "wohnungszustand"


def test_preset_already_set():
    result = decide(attrs={"climate.bt": {"preset_mode": "comfort", "temperature": 21.0}})
    assert result["reason"] == "bereits_erreicht"


def test_setpoint_already_set_and_clamped():
    already = decide(action_kind="setpoint", action_setpoint=21, attrs={"climate.bt": {"temperature": 21.0}})
    assert already["reason"] == "bereits_erreicht"
    high = decide(action_kind="setpoint", action_setpoint=30)
    assert high["target_value"] == 25.0 and high["payload"]["target"]["value"] == 25.0
    low = decide(action_kind="setpoint", action_setpoint=10)
    assert low["target_value"] == 15.0


@pytest.mark.parametrize("minutes,reason", [(20, "handeingriff"), (59, "handeingriff"), (60, ""), (61, "")])
def test_minimum_distance_to_the_last_intervention(minutes, reason):
    result = decide(attrs={"input_datetime.li": {"timestamp": minutes_ago(NOW, minutes)}})
    assert result["reason"] == reason


def test_other_distance_is_configurable():
    attrs = {"input_datetime.li": {"timestamp": minutes_ago(NOW, 20)}}
    assert decide(attrs=attrs, min_distance_min=10)["reason"] == ""


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
def test_unreachable_thermostat_is_not_touched(state):
    result = decide(states={"climate.bt": state})
    assert (result["reason"], result["will_act"]) == ("nicht_erreichbar", False)


def test_unreachable_wins_over_everything_else():
    result = decide(states={"climate.bt": "unavailable", "switch.rule": "off", "input_select.flat": "Vacation"})
    assert result["reason"] == "nicht_erreichbar"


def test_shadow_reports_what_it_would_have_done():
    result = decide(states={"switch.rule": "off"}, action_kind="setpoint", action_setpoint=22)
    assert result["payload"]["target"] == {"kind": "setpoint", "value": 22.0}
    assert result["payload"]["mode"] == "schatten"


def test_blueprint_structure():
    blueprint = load_blueprint(BLUEPRINT)
    assert set(blueprint["blueprint"]["input"]) == set(INPUTS)
    assert blueprint["blueprint"]["domain"] == "automation"
    assert blueprint["mode"] == "single"
    names = [step.get("alias") for step in blueprint["actions"]]
    assert names == ["Verzögerung", "Auf heating_control warten", "Bedingungen erneut prüfen", "Entscheidung",
                     "Ausführen", "Meldung"]
    dumped = json.dumps(blueprint["actions"], default=str)
    assert "climate.set_preset_mode" in dumped and "climate.set_temperature" in dumped
    assert "homeai/rule/{{ rule_id }}/fired" in dumped
