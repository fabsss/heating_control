from dataclasses import replace

import pytest

from analysis.generic import generic_vocabulary_text, parse_signal_condition, parse_signal_trigger
from analysis.models import STATUS_OPEN, STATUS_UNTESTABLE
from analysis.pseudonym import Pseudonyms
from analysis.signals import SignalInfo
from analysis.vocabulary import ParseContext, parse_candidate

SIGNALS = {
    "sig_01": SignalInfo("sig_01", "categorical", "Anwesenheit person_1", values=("away", "home")),
    "sig_02": SignalInfo("sig_02", "numeric", "Sensor", low=-5.0, high=30.0, terciles=(5.0, 15.0)),
    "sig_03": SignalInfo("sig_03", "timestamp", "Weckzeit"),
}
CTX = ParseContext(rooms={"Wohnzimmer": "r_living"}, pseudo=Pseudonyms((), {}, {}), presets=frozenset({"comfort"}),
                   flat_states=frozenset(), signals=SIGNALS, max_conditions=2)
ACTION = {"type": "setpoint", "value": 21}


def raw(trigger, conditions=None, **extra):
    return {"room": "Wohnzimmer", "trigger": trigger, "conditions": conditions or [], "action": ACTION, **extra}


def test_valid_signal_triggers_are_normalized():
    assert parse_signal_trigger({"signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": 180},
                                SIGNALS) == {"type": "signal", "signal": "sig_01", "event": "to_state",
                                             "value": "home", "min_prev_min": 180, "offset_min": 0}
    assert parse_signal_trigger({"signal": "sig_02", "event": "rises_above", "value": 12, "offset_min": 15},
                                SIGNALS) == {"type": "signal", "signal": "sig_02", "event": "rises_above",
                                             "value": 12.0, "offset_min": 15}
    assert parse_signal_trigger({"signal": "sig_03", "event": "alarm_time", "offset_min": -30},
                                SIGNALS)["offset_min"] == -30


@pytest.mark.parametrize("trigger", [
    {"signal": "sig_99", "event": "to_state", "value": "home"},                          # unbekanntes Signal
    {"signal": "sig_02", "event": "to_state", "value": "home"},                          # falsche Art
    {"signal": "sig_01", "event": "to_state", "value": "sleeping"},                      # unbekannter Zustand
    {"signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": 60, "max_prev_min": 30},
    {"signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": -5},
    {"signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": True},
    {"signal": "sig_02", "event": "rises_above", "value": 99},                           # außerhalb des Bereichs
    {"signal": "sig_02", "event": "rises_above", "value": "warm"},
    {"signal": "sig_01", "event": "to_state", "value": "home", "offset_min": -10},       # negativ nur bei alarm_time
    {"signal": "sig_01", "event": "to_state", "value": "home", "offset_min": 500},
    {"signal": "sig_03", "event": "alarm_time", "offset_min": -500},
    {"signal": "sig_01", "event": "explodiert"},
    {"event": "to_state", "value": "home"},
])
def test_invalid_signal_triggers_raise(trigger):
    with pytest.raises(ValueError):
        parse_signal_trigger(trigger, SIGNALS)


def test_signal_conditions():
    assert parse_signal_condition({"type": "signal_state", "signal": "sig_01", "op": "is_not", "value": "away"},
                                  SIGNALS) == {"type": "signal_state", "signal": "sig_01", "op": "is_not", "value": "away"}
    assert parse_signal_condition({"type": "signal_range", "signal": "sig_02", "op": "below", "value": 8},
                                  SIGNALS) == {"type": "signal_range", "signal": "sig_02", "op": "below", "value": 8.0}
    for bad in ({"type": "signal_state", "signal": "sig_02", "op": "is", "value": "x"},
                {"type": "signal_state", "signal": "sig_01", "op": "gleich", "value": "home"},
                {"type": "signal_range", "signal": "sig_01", "op": "below", "value": 8},
                {"type": "signal_range", "signal": "sig_02", "op": "darunter", "value": 8}):
        with pytest.raises(ValueError):
            parse_signal_condition(bad, SIGNALS)


def test_candidate_with_generic_forms_is_accepted():
    trigger = {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": 180}
    conditions = [{"type": "signal_range", "signal": "sig_02", "op": "below", "value": 8}]
    candidate = parse_candidate(raw(trigger, conditions, hypothesis="Heimkehr"), CTX)
    assert candidate.status == STATUS_OPEN and candidate.trigger["offset_min"] == 0
    assert candidate.conditions[0]["value"] == 8.0


def test_garbage_in_generic_candidates_is_untestable_not_an_error():
    for trigger in ({"type": "signal", "signal": "sig_99", "event": "to_state", "value": "home"},
                    {"type": "signal", "event": "to_state"},
                    {"type": "signal", "signal": "sig_02", "event": "rises_above", "value": 1e9}):
        candidate = parse_candidate(raw(trigger), CTX)
        assert candidate.status == STATUS_UNTESTABLE and candidate.note


def test_more_than_two_conditions_are_rejected_in_scan_mode_only():
    conditions = [{"type": "weekdays", "weekdays": [0]}] * 3
    trigger = {"type": "time", "weekdays": [0], "time": "17:30"}
    assert parse_candidate(raw(trigger, conditions), CTX).status == STATUS_UNTESTABLE
    assert parse_candidate(raw(trigger, conditions), replace(CTX, max_conditions=None)).status == STATUS_OPEN


def test_generic_forms_need_signals_in_the_context():
    trigger = {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home"}
    assert parse_candidate(raw(trigger), replace(CTX, signals={})).status == STATUS_UNTESTABLE


def test_vocabulary_text_names_every_form():
    text = generic_vocabulary_text()
    for needle in ("to_state", "rises_above", "falls_below", "alarm_time", "signal_state", "signal_range",
                   "offset_min", "min_prev_min", "setpoint", "preset"):
        assert needle in text
