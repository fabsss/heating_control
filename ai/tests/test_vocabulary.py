import pytest

from analysis.models import STATUS_OPEN, STATUS_UNTESTABLE
from analysis.pseudonym import Pseudonyms
from analysis.vocabulary import EVENT_NAMES, ParseContext, parse_candidate, parse_candidates, vocabulary_text

PSEUDO = Pseudonyms(("person.fabian", "person.svenja"), {}, {})
CTX = ParseContext(
    rooms={"Wohnzimmer": "r_living", "Bad": "r_bath"},
    pseudo=PSEUDO,
    presets=frozenset({"eco", "comfort"}),
    flat_states=frozenset({"home", "away"}),
)


def raw(**overrides):
    base = {
        "room": "Wohnzimmer",
        "trigger": {"type": "time", "weekdays": [4, 0, 1, 2, 3, 3], "time": "17:30"},
        "conditions": [],
        "action": {"type": "setpoint", "value": 21},
        "hypothesis": "Feierabend",
    }
    base.update(overrides)
    return base


def test_valid_time_candidate_is_normalised():
    candidate = parse_candidate(raw(), CTX)
    assert candidate.status == STATUS_OPEN and candidate.note == ""
    assert candidate.room == "r_living"
    assert candidate.trigger == {"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"}
    assert candidate.action == {"type": "setpoint", "value": 21.0}
    assert isinstance(candidate.action["value"], float)
    assert candidate.hypothesis == "Feierabend"


def test_person_alias_becomes_entity_id():
    trigger = {"type": "event", "event": "person_arrives", "person": "person_2"}
    candidate = parse_candidate(raw(trigger=trigger), CTX)
    assert candidate.status == STATUS_OPEN
    assert candidate.trigger == {"type": "event", "event": "person_arrives", "person": "person.svenja"}


@pytest.mark.parametrize("event", ["bed_in", "bed_out", "window_closed", "office_day_start"])
def test_plain_events_are_valid(event):
    candidate = parse_candidate(raw(trigger={"type": "event", "event": event}), CTX)
    assert candidate.status == STATUS_OPEN
    assert candidate.trigger == {"type": "event", "event": event}


def test_flat_state_event_needs_a_known_state():
    good = parse_candidate(raw(trigger={"type": "event", "event": "flat_state", "value": "away"}), CTX)
    assert good.status == STATUS_OPEN and good.trigger["value"] == "away"
    bad = parse_candidate(raw(trigger={"type": "event", "event": "flat_state", "value": "party"}), CTX)
    assert bad.status == STATUS_UNTESTABLE


def test_all_condition_types_are_accepted():
    conditions = [
        {"type": "weekdays", "weekdays": [5, 6]},
        {"type": "time_window", "from": "16:00", "to": "20:00"},
        {"type": "outside_temp", "op": "below", "value": 8},
        {"type": "person_home", "person": "person_1", "home": True},
    ]
    candidate = parse_candidate(raw(conditions=conditions), CTX)
    assert candidate.status == STATUS_OPEN
    assert candidate.conditions == [
        {"type": "weekdays", "weekdays": [5, 6]},
        {"type": "time_window", "from": "16:00", "to": "20:00"},
        {"type": "outside_temp", "op": "below", "value": 8.0},
        {"type": "person_home", "person": "person.fabian", "home": True},
    ]


def test_preset_action_must_exist_in_the_data():
    assert parse_candidate(raw(action={"type": "preset", "value": "comfort"}), CTX).status == STATUS_OPEN
    assert parse_candidate(raw(action={"type": "preset", "value": "party"}), CTX).status == STATUS_UNTESTABLE


@pytest.mark.parametrize("overrides", [
    {"room": "Keller"},
    {"room": None},
    {"room": ["Wohnzimmer"]},
    {"trigger": {"type": "magic"}},
    {"trigger": None},
    {"trigger": {"type": "time", "weekdays": [0], "time": "25:99"}},
    {"trigger": {"type": "time", "weekdays": [9], "time": "08:00"}},
    {"trigger": {"type": "time", "weekdays": [], "time": "08:00"}},
    {"trigger": {"type": "time", "weekdays": [True], "time": "08:00"}},
    {"trigger": {"type": "event", "event": "door_opens"}},
    {"trigger": {"type": "event", "event": "person_arrives", "person": "person_9"}},
    {"trigger": {"type": "event", "event": "person_arrives"}},
    {"action": {"type": "setpoint", "value": 99}},
    {"action": {"type": "setpoint", "value": "warm"}},
    {"action": {"type": "setpoint", "value": True}},
    {"action": {"type": "dim", "value": 5}},
    {"action": {"type": "preset", "value": ["comfort"]}},
    {"trigger": {"type": "event", "event": "flat_state", "value": ["away"]}},
    {"action": None},
    {"conditions": "immer"},
    {"conditions": [{"type": "outside_temp", "op": "equals", "value": 8}]},
    {"conditions": [{"type": "outside_temp", "op": "below", "value": "kalt"}]},
    {"conditions": [{"type": "time_window", "from": "20:00", "to": "16:00"}]},
    {"conditions": [{"type": "person_home", "person": "person_1"}]},
    {"conditions": [{"type": "lunar_phase"}]},
])
def test_parse_candidate_marks_garbage_as_untestable(overrides):
    candidate = parse_candidate(raw(**overrides), CTX)
    assert candidate.status == STATUS_UNTESTABLE
    assert candidate.note


@pytest.mark.parametrize("value", [None, 5, "text", [], [1, 2]])
def test_non_object_entries_never_raise(value):
    candidate = parse_candidate(value, CTX)
    assert candidate.status == STATUS_UNTESTABLE and candidate.note


def test_hypothesis_is_truncated_and_missing_hypothesis_is_empty():
    assert len(parse_candidate(raw(hypothesis="x" * 2000), CTX).hypothesis) == 500
    without = raw()
    del without["hypothesis"]
    assert parse_candidate(without, CTX).hypothesis == ""


def test_parse_candidates_keeps_order_and_mixes_status():
    result = parse_candidates([raw(), raw(room="Keller")], CTX)
    assert [c.status for c in result] == [STATUS_OPEN, STATUS_UNTESTABLE]


def test_vocabulary_text_names_every_building_block():
    text = vocabulary_text()
    for name in (*EVENT_NAMES, "time_window", "outside_temp", "person_home", "weekdays", "preset", "setpoint"):
        assert name in text
