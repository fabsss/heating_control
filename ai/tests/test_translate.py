import pytest

from analysis.models import Candidate
from collector.models import EntityRule
from execution.translate import (RuleInputs, SignalRef, TranslateError, climate_for_room, load_signal_refs,
                                 translate)
from tests.ha_template import utc

SIGNALS = {
    "sig_01": SignalRef("sig_01", "person.fabian", "person", "categorical"),
    "sig_02": SignalRef("sig_02", "sensor.pixel_9a_next_alarm", "sensor", "timestamp"),
    "sig_03": SignalRef("sig_03", "sensor.outside_temp", "sensor", "numeric"),
    "sig_04": SignalRef("sig_04", "input_select.flat_occupation_state", "flat", "categorical"),
}
LIMITS = dict(setpoint_min=15.0, setpoint_max=25.0)
PRESET = {"type": "preset", "value": "comfort"}


def rule(trigger, conditions=None, action=PRESET):
    return Candidate(room="bad", trigger=trigger, conditions=conditions or [], action=action)


def run(trigger, conditions=None, action=PRESET) -> RuleInputs:
    return translate(rule(trigger, conditions, action), SIGNALS, **LIMITS)


TRUE = {"condition": "template", "value_template": "{{ true }}"}


def test_time_trigger_with_weekdays():
    result = run({"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "06:30"})
    assert result.triggers == [{"trigger": "time", "at": "06:30:00"}]
    assert result.conditions == [{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]}]
    assert (result.action_kind, result.action_preset, result.delay_min) == ("preset", "comfort", 0)


def test_daily_time_trigger_needs_no_weekday_condition_but_never_has_empty_conditions():
    result = run({"type": "time", "weekdays": [0, 1, 2, 3, 4, 5, 6], "time": "17:00"})
    assert result.conditions == [TRUE]


def test_person_arrival_after_a_long_absence():
    result = run({"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home",
                  "min_prev_min": 180, "offset_min": 0})
    assert result.triggers == [{"trigger": "state", "entity_id": "person.fabian", "to": "home",
                                "not_from": ["unavailable", "unknown"]}]
    (condition,) = result.conditions
    assert condition["condition"] == "template"
    assert "trigger.from_state is not none" in condition["value_template"]
    assert ">= 180" in condition["value_template"] and "< " not in condition["value_template"]


def test_person_leaving_means_from_home():
    result = run({"type": "signal", "signal": "sig_01", "event": "to_state", "value": "away", "offset_min": 0})
    assert result.triggers == [{"trigger": "state", "entity_id": "person.fabian", "from": "home",
                                "not_to": ["unavailable", "unknown"]}]


def test_duration_range_and_positive_offset_become_condition_and_delay():
    result = run({"type": "signal", "signal": "sig_04", "event": "to_state", "value": "Home",
                  "min_prev_min": 45, "max_prev_min": 180, "offset_min": 15})
    assert result.delay_min == 15
    template = result.conditions[0]["value_template"]
    assert ">= 45" in template and "< 180" in template


def test_alarm_time_uses_the_timestamp_sensor_with_a_signed_offset():
    result = run({"type": "signal", "signal": "sig_02", "event": "alarm_time", "offset_min": -30})
    assert result.triggers == [{"trigger": "time", "at": "sensor.pixel_9a_next_alarm", "offset": "-00:30:00"}]
    assert result.delay_min == 0
    later = run({"type": "signal", "signal": "sig_02", "event": "alarm_time", "offset_min": 90})
    assert later.triggers[0]["offset"] == "01:30:00"
    plain = run({"type": "signal", "signal": "sig_02", "event": "alarm_time", "offset_min": 0})
    assert "offset" not in plain.triggers[0]


def test_numeric_thresholds():
    up = run({"type": "signal", "signal": "sig_03", "event": "rises_above", "value": 18.5, "offset_min": 10})
    assert up.triggers == [{"trigger": "numeric_state", "entity_id": "sensor.outside_temp", "above": 18.5}]
    assert up.delay_min == 10
    down = run({"type": "signal", "signal": "sig_03", "event": "falls_below", "value": 8, "offset_min": 0})
    assert down.triggers[0]["below"] == 8.0


def test_conditions_are_translated_in_order():
    result = run(
        {"type": "time", "weekdays": [5, 6], "time": "09:00"},
        [{"type": "weekdays", "weekdays": [5]},
         {"type": "time_window", "from": "08:00", "to": "12:00"},
         {"type": "signal_state", "signal": "sig_04", "op": "is_not", "value": "Away"},
         {"type": "signal_range", "signal": "sig_03", "op": "below", "value": 8}])
    assert result.conditions == [
        {"condition": "time", "weekday": ["sat", "sun"]},
        {"condition": "time", "weekday": ["sat"]},
        {"condition": "time", "after": "08:00:00", "before": "12:00:00"},
        {"condition": "not", "conditions": [
            {"condition": "state", "entity_id": "input_select.flat_occupation_state", "state": "Away"}]},
        {"condition": "numeric_state", "entity_id": "sensor.outside_temp", "below": 8.0},
    ]


def test_person_state_conditions():
    away = run({"type": "time", "weekdays": [0], "time": "07:00"},
               [{"type": "signal_state", "signal": "sig_01", "op": "is", "value": "away"}])
    assert away.conditions[-1] == {"condition": "not", "conditions": [
        {"condition": "state", "entity_id": "person.fabian", "state": "home"}]}
    home = run({"type": "time", "weekdays": [0], "time": "07:00"},
               [{"type": "signal_state", "signal": "sig_01", "op": "is_not", "value": "away"}])
    assert home.conditions[-1] == {"condition": "state", "entity_id": "person.fabian", "state": "home"}


def test_setpoint_action():
    result = run({"type": "time", "weekdays": [0], "time": "07:00"}, action={"type": "setpoint", "value": 21.5})
    assert (result.action_kind, result.action_setpoint) == ("setpoint", 21.5)


@pytest.mark.parametrize("trigger,match", [
    ({"type": "event", "event": "person_arrives", "person": "person.fabian"}, "alte"),
    ({"type": "signal", "signal": "sig_99", "event": "to_state", "value": "x"}, "sig_99"),
    ({"type": "nonsense"}, "Auslöser"),
])
def test_unsupported_triggers_are_rejected_with_a_clear_message(trigger, match):
    with pytest.raises(TranslateError, match=match):
        run(trigger)


def test_legacy_conditions_and_bad_actions_are_rejected():
    time = {"type": "time", "weekdays": [0], "time": "07:00"}
    with pytest.raises(TranslateError, match="alte"):
        run(time, [{"type": "outside_temp", "op": "below", "value": 8}])
    with pytest.raises(TranslateError, match="Sollwert"):
        run(time, action={"type": "setpoint", "value": 30.0})
    with pytest.raises(TranslateError, match="Preset"):
        run(time, action={"type": "preset", "value": ""})
    with pytest.raises(TranslateError, match="Aktion"):
        run(time, action={"type": "dance"})


def test_signal_refs_and_climate_lookup_come_from_the_database(db, store):
    db.upsert_entity(EntityRule("climate.better_thermostat_bathroom", "climate", "heating"), "bad", utc(2026, 1, 1))
    db.upsert_entity(EntityRule("climate.better_thermostat_office", "climate", "heating"), "office", utc(2026, 1, 1))
    handles = store.assign_handles(["person.fabian"])
    store.update_signal(handles["person.fabian"], "person", "categorical", "Anwesenheit person_1")
    refs = load_signal_refs(db)
    assert refs["sig_01"] == SignalRef("sig_01", "person.fabian", "person", "categorical")
    assert climate_for_room(db, "bad") == "climate.better_thermostat_bathroom"
    with pytest.raises(TranslateError, match="Heizkörper"):
        climate_for_room(db, "keller")
    db.upsert_entity(EntityRule("climate.second", "climate", "heating"), "bad", utc(2026, 1, 1))
    with pytest.raises(TranslateError, match="mehrere"):
        climate_for_room(db, "bad")
