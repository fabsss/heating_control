from execution.helpers import HelperNames, eingriff_automations, helper_names, helper_specs, slug
from tests.ha_template import HaEnv, utc

NOW = utc(2026, 10, 15, 12, 0)
CLIMATE = "climate.better_thermostat_bathroom"


def test_slug_and_names():
    assert slug("Küche & Bad") == "k_che_bad"
    assert helper_names("Bad") == HelperNames("ki_regeln_bad", "ki_eingriff_bad", "ki_aufruf_bad")


def test_helper_specs_cover_toggle_and_two_datetimes():
    specs = helper_specs("bad")
    assert [(domain, name) for domain, name, _ in specs] == [
        ("input_boolean", "ki_regeln_bad"), ("input_datetime", "ki_eingriff_bad"), ("input_datetime", "ki_aufruf_bad")]
    assert specs[1][2]["has_date"] is True and specs[1][2]["has_time"] is True


def automations():
    return dict(eingriff_automations("bad", CLIMATE))


def test_two_automations_with_stable_ids():
    assert list(automations()) == ["homeai_aufrufe_bad", "homeai_beobachter_bad"]


def trigger_event(entity_ids, *, user=None, parent=None):
    return {"event": {"data": {"domain": "climate", "service": "set_temperature",
                               "service_data": {} if entity_ids is None else {"entity_id": entity_ids}},
                      "context": {"user_id": user, "parent_id": parent}}}


def call_condition(entity_ids):
    config = automations()["homeai_aufrufe_bad"]
    env = HaEnv({}, {}, NOW)
    return env.render(config["conditions"][0]["value_template"], {"trigger": trigger_event(entity_ids)})


def test_call_automation_matches_list_and_string_targets_only_for_its_thermostat():
    assert call_condition([CLIMATE]) is True
    assert call_condition(CLIMATE) is True
    assert call_condition(["climate.other"]) is False
    assert call_condition(None) is False


def route(user, parent):
    config = automations()["homeai_aufrufe_bad"]
    branch = config["actions"][0]["choose"][0]
    env = HaEnv({}, {}, NOW)
    is_user = env.render(branch["conditions"][0]["value_template"], {"trigger": trigger_event([CLIMATE], user=user, parent=parent)})
    return branch["sequence"][0]["target"]["entity_id"] if is_user else config["actions"][0]["default"][0]["target"]["entity_id"]


def test_user_calls_set_the_intervention_stamp_and_automation_calls_the_other():
    assert route("u1", None) == "input_datetime.ki_eingriff_bad"
    assert route(None, None) == "input_datetime.ki_aufruf_bad"
    assert route("u1", "p1") == "input_datetime.ki_aufruf_bad"
    assert route(None, "p1") == "input_datetime.ki_aufruf_bad"


def observer_condition(old, new):
    config = automations()["homeai_beobachter_bad"]
    env = HaEnv({}, {}, NOW)
    trigger = {"from_state": old, "to_state": new}
    return env.render(config["conditions"][0]["value_template"], {"trigger": trigger})


def snap(state="heat", temperature=21.0, preset="comfort"):
    return {"state": state, "attributes": {"temperature": temperature, "preset_mode": preset}}


def test_observer_reacts_to_setpoint_preset_and_mode_changes_only():
    assert observer_condition(snap(), snap(temperature=22.0)) is True
    assert observer_condition(snap(), snap(preset="eco")) is True
    assert observer_condition(snap(), snap(state="off")) is True
    assert observer_condition(snap(), snap()) is False
    attributes = {"state": "heat", "attributes": {"temperature": 21.0, "preset_mode": "comfort", "window_open": True}}
    assert observer_condition(snap(), attributes) is False


def test_observer_ignores_unavailable_transitions_and_missing_old_state():
    assert observer_condition(snap(state="unavailable"), snap()) is False
    assert observer_condition(snap(), snap(state="unknown", temperature=1.0)) is False
    assert observer_condition(None, snap()) is False


def observer_decides_hand(minutes_since_intervention, minutes_since_auto):
    config = automations()["homeai_beobachter_bad"]
    env = HaEnv({}, {
        "input_datetime.ki_eingriff_bad": {"timestamp": (NOW.timestamp() - minutes_since_intervention * 60)},
        "input_datetime.ki_aufruf_bad": {"timestamp": (NOW.timestamp() - minutes_since_auto * 60)},
    }, NOW)
    return env.render(config["actions"][1]["value_template"], {})


def test_observer_only_marks_a_hand_intervention_without_a_recent_call():
    assert observer_decides_hand(60, 60) is True
    assert observer_decides_hand(0.05, 60) is False          # Nutzeraufruf vor 3 Sekunden
    assert observer_decides_hand(60, 0.05) is False          # Automationsaufruf vor 3 Sekunden
    assert observer_decides_hand(60, 0.2) is True            # Aufruf vor 12 Sekunden zählt nicht mehr


def test_observer_waits_three_seconds_and_writes_the_intervention_stamp():
    config = automations()["homeai_beobachter_bad"]
    assert config["actions"][0] == {"delay": {"seconds": 3}}
    write = config["actions"][2]
    assert write["action"] == "input_datetime.set_datetime"
    assert write["target"]["entity_id"] == "input_datetime.ki_eingriff_bad"
    assert config["mode"] == "queued"
