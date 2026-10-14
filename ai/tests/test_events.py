from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from analysis.config import ScanParams
from analysis.events import (ALL, EventSpec, class_labels, class_of, event_series, event_text, signal_events)
from analysis.signals import Signal, SignalInfo, SignalLibrary
from analysis.timeline import Timeline

T0 = datetime(2026, 1, 5, 6, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def make(value_kind, points, **extra):
    info = SignalInfo("sig_01", value_kind, "Test", **extra)
    return Signal(info, "x.y", "env", None, Timeline([(at(minutes), state) for minutes, state in points]))


def test_class_labels_and_assignment():
    assert class_labels((45, 180)) == ["<45", "45-180", "≥180"]
    assert [class_of(m, (45, 180)) for m in (0, 44.9, 45, 179, 180, 5000)] == ["<45", "<45", "45-180", "45-180", "≥180", "≥180"]
    assert class_labels((60,)) == ["<60", "≥60"]


def test_to_state_events_carry_the_duration_of_the_previous_state():
    signal = make("categorical", [(0, "home"), (600, "away"), (620, "home"), (800, "away"), (1000, "home")],
                  values=("away", "home"))
    events = signal_events(signal, "to_state", "home")
    assert [(ts, round(prev)) for ts, prev in events] == [(at(620), 20), (at(1000), 200)]


def test_attribute_only_rows_do_not_restart_the_state_duration():
    signal = make("categorical", [(0, "away"), (100, "away"), (200, "home")], values=("away", "home"))
    assert [round(prev) for _, prev in signal_events(signal, "to_state", "home")] == [200]


def test_changes_from_an_unknown_state_are_not_events():
    signal = make("categorical", [(0, "home"), (100, "away"), (200, None), (300, "home")], values=("away", "home"))
    assert signal_events(signal, "to_state", "home") == []


def test_threshold_crossings_are_directional_and_throttled():
    points = [(0, "5"), (10, "9"), (20, "5"), (30, "9"), (200, "5"), (300, "9")]
    signal = make("numeric", points, low=5.0, high=9.0, terciles=(6.0, 8.0))
    rising = signal_events(signal, "rises_above", 8.0)
    assert [ts for ts, _ in rising] == [at(10), at(300)]                 # 30 liegt innerhalb einer Stunde nach 10
    assert [ts for ts, _ in signal_events(signal, "falls_below", 8.0)] == [at(20), at(200)]


def test_unknown_values_break_a_crossing():
    signal = make("numeric", [(0, "5"), (10, None), (20, "9")], low=5.0, high=9.0, terciles=(6.0, 8.0))
    assert signal_events(signal, "rises_above", 8.0) == []


def test_alarm_event_happens_at_the_alarm_time_if_it_stayed_set():
    alarm = "2026-01-05T08:00:00+00:00"                                     # T0 ist 06:00 UTC
    signal = make("timestamp", [(0, alarm), (300, "2026-01-06T08:00:00+00:00")])
    assert [ts for ts, _ in signal_events(signal, "alarm_time", until=T0 + timedelta(hours=10))] == [T0 + timedelta(hours=2)]


def test_deleted_or_moved_alarms_do_not_count():
    deleted = make("timestamp", [(0, "2026-01-05T08:00:00+00:00"), (60, None)])
    assert signal_events(deleted, "alarm_time") == []
    moved = make("timestamp", [(0, "2026-01-05T08:00:00+00:00"), (60, "2026-01-05T09:00:00+00:00")])
    assert [ts for ts, _ in signal_events(moved, "alarm_time")] == [T0 + timedelta(hours=3)]


def test_future_alarms_and_alarms_after_until_are_ignored():
    signal = make("timestamp", [(0, "2026-01-05T08:00:00+00:00")])
    assert signal_events(signal, "alarm_time", until=T0 + timedelta(hours=1)) == []
    in_past = make("timestamp", [(0, "2026-01-05T05:00:00+00:00")])
    assert signal_events(in_past, "alarm_time") == []


def test_unknown_event_name_raises():
    with pytest.raises(ValueError):
        signal_events(make("categorical", [(0, "a"), (10, "b")]), "explodiert", "a")


def library(*signals):
    return SignalLibrary(list(signals))


def test_event_series_for_categorical_signals_include_duration_classes(acfg):
    params = replace(acfg.scan, min_events=3)
    points = []
    minutes = 0
    for index in range(4):                                                  # vier Abwesenheiten à 20 Minuten
        points += [(minutes, "home"), (minutes + 100, "away")]
        minutes += 120
    points.append((minutes, "home"))
    signal = make("categorical", points, values=("away", "home"))
    series = event_series(library(signal), params)
    keys = {(item.spec.value, item.spec.prev_class): len(item.times) for item in series}
    assert keys[("home", ALL)] == 4 and keys[("home", "<45")] == 4 and keys[("away", ALL)] == 4
    assert ("home", "≥180") not in keys                                      # unter min_events


def test_event_series_for_numeric_and_timestamp_signals(acfg):
    params = replace(acfg.scan, min_events=2)
    numbers = make("numeric", [(i * 120, str(5 + (i % 2) * 5)) for i in range(12)], low=5.0, high=10.0,
                   terciles=(5.0, 10.0))
    alarms = make("timestamp", [(i * 1440, f"2026-01-{5 + i:02d}T08:00:00+00:00") for i in range(5)])
    alarms.info = replace(alarms.info, handle="sig_02")
    specs = {(item.spec.signal, item.spec.event, item.spec.value) for item in event_series(library(numbers, alarms), params)}
    assert {spec for spec in specs if spec[0] == "sig_01"} == {("sig_01", "rises_above", "10"), ("sig_01", "falls_below", "10")}
    assert ("sig_02", "alarm_time", None) in specs


def test_event_text():
    assert event_text(EventSpec("s", "to_state", "home", ALL)) == "wechselt zu home"
    assert "vorher ≥180 min" in event_text(EventSpec("s", "to_state", "home", "≥180"))
    assert event_text(EventSpec("s", "rises_above", "18", ALL)) == "steigt über 18"
    assert event_text(EventSpec("s", "falls_below", "5", ALL)) == "fällt unter 5"
    assert event_text(EventSpec("s", "alarm_time", None, ALL)) == "Weckzeit erreicht"


def test_flapping_state_changes_are_thinned_like_crossings():
    points = [(0, "off"), (10, "on"), (12, "off"), (14, "on"), (16, "off"), (400, "on")]
    signal = make("categorical", points, values=("off", "on"))
    assert [ts for ts, _ in signal_events(signal, "to_state", "on")] == [at(10), at(400)]
