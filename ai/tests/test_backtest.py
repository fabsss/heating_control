from dataclasses import replace
from datetime import date, timedelta

import pytest

from analysis.backtest import Backtester
from analysis.config import Exclusion
from analysis.context import ContextProvider
from analysis.models import (STATUS_FEW, STATUS_PASSED, STATUS_REJECTED, STATUS_UNTESTABLE,
                             Candidate, Intervention)
from analysis.pseudonym import Pseudonyms
from analysis.timeline import Timeline
from tests.analysis_fixture import local_utc

BASE = date(2026, 1, 5)                      # ein Montag, Winterzeit
START = local_utc(BASE, "00:00")
END = local_utc(BASE + timedelta(days=42), "00:00")


def day(n):
    return BASE + timedelta(days=n)


def iv(n, hhmm, value=21.0, kind="setpoint", room="r"):
    return Intervention(local_utc(day(n), hhmm), room, "climate.x", kind, value)


def rule(trigger, *, action=None, conditions=None, room="r"):
    return Candidate(room=room, trigger=trigger, conditions=conditions or [],
                     action=action or {"type": "setpoint", "value": 21.0})


def time_rule(hhmm="17:30", weekdays=(0, 1, 2, 3, 4)):
    return {"type": "time", "weekdays": list(weekdays), "time": hhmm}


def backtester(cfg, interventions, ctx=None, start=START, end=END):
    return Backtester(cfg, interventions, ctx or ContextProvider.empty(), start, end)


def weekday_interventions(hhmm="17:32", value=21.0):
    return [iv(n, hhmm, value) for n in range(42) if day(n).weekday() < 5]


def with_persons(timelines):
    return ContextProvider(Pseudonyms(tuple(timelines), {}, {}), persons=timelines)


def test_time_trigger_with_matching_habit_passes(acfg):
    result = backtester(acfg, weekday_interventions()).evaluate(rule(time_rule()))
    assert result.status == STATUS_PASSED
    assert result.metrics == {"ausloesungen": 30.0, "faelle": 30.0, "wochen": 6.0,
                              "eingriffe_gesamt": 30.0, "abdeckung": 1.0, "fehlausloesungen": 0.0}


def test_time_trigger_follows_local_time_across_dst(acfg):
    start, end = local_utc(date(2026, 3, 24), "00:00"), local_utc(date(2026, 4, 3), "00:00")
    items = [Intervention(local_utc(date(2026, 3, 25) + timedelta(days=k), "07:05"),
                          "r", "climate.x", "setpoint", 21.0) for k in range(9)]   # 25.03. bis 02.04.
    result = backtester(acfg, items, start=start, end=end).evaluate(
        rule(time_rule("07:00", range(7)), action={"type": "setpoint", "value": 21.0}))
    assert result.metrics["faelle"] == 9.0               # Sommerzeit am 29.03. verschiebt nichts
    assert result.metrics["abdeckung"] == 1.0
    assert result.metrics["fehlausloesungen"] == pytest.approx(0.1)   # 24.03. ohne Eingriff


def test_trigger_without_any_intervention_is_a_false_trigger(acfg):
    result = backtester(acfg, weekday_interventions()).evaluate(rule(time_rule("09:00")))
    assert result.status == STATUS_FEW
    assert result.metrics["faelle"] == 0.0 and result.metrics["fehlausloesungen"] == 1.0


def test_contrary_intervention_within_an_hour_counts_as_false_trigger(acfg):
    items = [iv(n, "17:32", 21.0) for n in range(10)] + [iv(n, "18:00", 24.0) for n in range(10)]
    ten_days = local_utc(day(10), "00:00")              # genau die zehn Tage mit Eingriffen
    result = backtester(acfg, items, end=ten_days).evaluate(rule(time_rule(weekdays=range(7))))
    assert result.metrics["faelle"] == 10.0
    assert result.metrics["fehlausloesungen"] == 1.0


def test_low_coverage_is_rejected(acfg):
    items = [iv(7 * week, "17:32") for week in range(6)]                  # nur 6 Fälle, je eine Woche
    items += [iv(n, "10:00") for n in range(42) if day(n).weekday() < 5]  # 30 Eingriffe zu anderer Zeit
    result = backtester(acfg, items).evaluate(rule(time_rule(weekdays=range(7))))
    assert result.metrics["faelle"] == 6.0 and result.metrics["wochen"] == 6.0
    assert result.metrics["abdeckung"] == pytest.approx(6 / 36)
    assert result.status == STATUS_REJECTED


def test_too_few_cases_or_weeks_means_not_enough_evidence(acfg):
    items = [iv(n, "17:32") for n in range(4)]
    result = backtester(acfg, items).evaluate(rule(time_rule(weekdays=range(7))))
    assert result.status == STATUS_FEW and "4" in result.note
    two_weeks = [iv(n, "17:32") for n in range(10)]
    result = backtester(acfg, two_weeks).evaluate(rule(time_rule(weekdays=range(7))))
    assert result.metrics["faelle"] == 10.0 and result.metrics["wochen"] == 2.0
    assert result.status == STATUS_FEW


def test_preset_action_matches_exact_value_only(acfg):
    items = [iv(n, "17:32", "comfort", kind="preset") for n in range(42) if day(n).weekday() < 5]
    good = backtester(acfg, items).evaluate(rule(time_rule(), action={"type": "preset", "value": "comfort"}))
    assert good.status == STATUS_PASSED
    other = backtester(acfg, items).evaluate(rule(time_rule(), action={"type": "preset", "value": "eco"}))
    assert other.metrics["faelle"] == 0.0


def test_setpoint_tolerance_is_respected(acfg):
    items = weekday_interventions(value=21.4)
    assert backtester(acfg, items).evaluate(rule(time_rule())).metrics["faelle"] == 30.0
    far = weekday_interventions(value=22.0)
    assert backtester(acfg, far).evaluate(rule(time_rule())).metrics["faelle"] == 0.0


def arrival_context(days=21):
    points = []
    for n in range(days):
        points += [(local_utc(day(n), "07:00"), "not_home"), (local_utc(day(n), "17:20"), "home")]
    return with_persons({"person.svenja": Timeline(points)})


def test_person_arrives_trigger_passes_for_the_known_habit(acfg):
    items = [iv(n, "17:35") for n in range(21)]
    trigger = {"type": "event", "event": "person_arrives", "person": "person.svenja"}
    result = backtester(acfg, items, arrival_context()).evaluate(rule(trigger))
    assert result.status == STATUS_PASSED
    assert result.metrics["ausloesungen"] == 21.0 and result.metrics["wochen"] == 3.0


def test_person_leaves_uses_the_departure_instant(acfg):
    points = []
    for n in range(21):
        points += [(local_utc(day(n), "07:00"), "home"), (local_utc(day(n), "08:10"), "not_home")]
    ctx = with_persons({"person.svenja": Timeline(points)})
    items = [iv(n, "08:20", 17.0) for n in range(21)]
    trigger = {"type": "event", "event": "person_leaves", "person": "person.svenja"}
    result = backtester(acfg, items, ctx).evaluate(rule(trigger, action={"type": "setpoint", "value": 17.0}))
    assert result.metrics["faelle"] == 21.0


def test_conditions_filter_the_triggers(acfg):
    items = [iv(n, "17:35") for n in range(21)]
    trigger = {"type": "event", "event": "person_arrives", "person": "person.svenja"}
    weekdays = [{"type": "weekdays", "weekdays": [0, 1, 2, 3, 4]}]
    result = backtester(acfg, items, arrival_context()).evaluate(rule(trigger, conditions=weekdays))
    assert result.metrics["ausloesungen"] == 15.0
    window = [{"type": "time_window", "from": "18:00", "to": "23:00"}]
    result = backtester(acfg, items, arrival_context()).evaluate(rule(trigger, conditions=window))
    assert result.metrics["ausloesungen"] == 0.0


def test_outside_temperature_condition(acfg):
    ctx = ContextProvider.empty()
    ctx.outside_temp = Timeline([(local_utc(day(0), "00:00"), "5.0"), (local_utc(day(7), "00:00"), "12.0")])
    below = [{"type": "outside_temp", "op": "below", "value": 8}]
    above = [{"type": "outside_temp", "op": "above", "value": 8}]
    cold = backtester(acfg, [], ctx).evaluate(rule(time_rule(weekdays=range(7)), conditions=below))
    warm = backtester(acfg, [], ctx).evaluate(rule(time_rule(weekdays=range(7)), conditions=above))
    assert cold.metrics["ausloesungen"] == 7.0 and warm.metrics["ausloesungen"] == 35.0
    unknown = backtester(acfg, []).evaluate(rule(time_rule(weekdays=range(7)), conditions=below))
    assert unknown.status == STATUS_UNTESTABLE            # ohne Außensensor nicht prüfbar, statt still 0 Fälle


def test_person_home_condition(acfg):
    ctx = arrival_context()
    three_weeks = local_utc(day(21), "00:00")           # so lange gibt es Daten für die Person
    home = [{"type": "person_home", "person": "person.svenja", "home": True}]
    result = backtester(acfg, [], ctx, end=three_weeks).evaluate(rule(time_rule("18:00", range(7)), conditions=home))
    assert result.metrics["ausloesungen"] == 21.0        # um 18:00 ist sie zu Hause
    away = [{"type": "person_home", "person": "person.svenja", "home": False}]
    result = backtester(acfg, [], ctx, end=three_weeks).evaluate(rule(time_rule("12:00", range(7)), conditions=away))
    assert result.metrics["ausloesungen"] == 21.0
    result = backtester(acfg, [], ctx, end=three_weeks).evaluate(rule(time_rule("12:00", range(7)), conditions=home))
    assert result.metrics["ausloesungen"] == 0.0         # mittags ist sie nicht zu Hause


def test_flat_state_bed_and_office_events(acfg):
    ctx = ContextProvider.empty()
    ctx.flat_state = Timeline([(local_utc(day(0), "08:00"), "home"), (local_utc(day(1), "08:00"), "away"),
                               (local_utc(day(2), "08:00"), "home"), (local_utc(day(3), "08:00"), "away")])
    ctx.in_bed = Timeline([(local_utc(day(0), "22:00"), "off"), (local_utc(day(0), "23:00"), "on"),
                           (local_utc(day(1), "07:00"), "off")])
    ctx.office_day = Timeline([(local_utc(day(0), "06:00"), "off"), (local_utc(day(2), "06:00"), "on")])
    flat = backtester(acfg, [], ctx).evaluate(rule({"type": "event", "event": "flat_state", "value": "away"}))
    assert flat.metrics["ausloesungen"] == 2.0
    for event, count in (("bed_in", 1.0), ("bed_out", 1.0), ("office_day_start", 1.0)):
        assert backtester(acfg, [], ctx).evaluate(rule({"type": "event", "event": event})).metrics[
            "ausloesungen"] == count


def test_window_closed_trigger_waits_for_the_last_window(acfg):
    windows = {"r": [
        Timeline([(local_utc(day(n), "17:00"), "on") for n in range(21)]
                 + [(local_utc(day(n), "17:10"), "off") for n in range(21)]),
        Timeline([(local_utc(day(n), "17:05"), "on") for n in range(21)]
                 + [(local_utc(day(n), "17:20"), "off") for n in range(21)]),
    ]}
    ctx = ContextProvider(Pseudonyms((), {}, {}), windows=windows)
    items = [iv(n, "17:25", 19.0) for n in range(21)]
    result = backtester(acfg, items, ctx).evaluate(
        rule({"type": "event", "event": "window_closed"}, action={"type": "setpoint", "value": 19.0}))
    assert result.metrics["ausloesungen"] == 21.0         # 17:20, nicht 17:10
    assert result.metrics["faelle"] == 21.0


def test_missing_window_sensors_make_candidate_untestable(acfg):
    result = backtester(acfg, weekday_interventions()).evaluate(rule({"type": "event", "event": "window_closed"}))
    assert result.status == STATUS_UNTESTABLE and result.metrics == {} and result.note


@pytest.mark.parametrize("trigger", [
    {"type": "event", "event": "person_arrives", "person": "person.unbekannt"},
    {"type": "event", "event": "flat_state", "value": "away"},
    {"type": "event", "event": "bed_in"},
    {"type": "event", "event": "bed_out"},
    {"type": "event", "event": "office_day_start"},
])
def test_missing_context_data_makes_event_candidates_untestable(acfg, trigger):
    result = backtester(acfg, weekday_interventions()).evaluate(rule(trigger))
    assert result.status == STATUS_UNTESTABLE and result.note


def test_exclusion_removes_triggers_of_that_room_only(acfg):
    cfg = replace(acfg, exclusions=(Exclusion("r", local_utc(day(21), "00:00"), None),))
    result = backtester(cfg, []).evaluate(rule(time_rule(weekdays=range(7))))
    assert result.metrics["ausloesungen"] == 21.0
    other = replace(acfg, exclusions=(Exclusion("andere", START, None),))
    assert backtester(other, []).evaluate(rule(time_rule(weekdays=range(7)))).metrics["ausloesungen"] == 42.0


def test_interventions_of_other_rooms_and_kinds_are_ignored(acfg):
    items = weekday_interventions()
    other_room = [Intervention(i.ts, "anderer", i.entity_id, i.kind, i.value) for i in items]
    assert backtester(acfg, other_room).evaluate(rule(time_rule())).metrics["faelle"] == 0.0
    other_kind = [Intervention(i.ts, i.room, i.entity_id, "preset", "comfort") for i in items]
    assert backtester(acfg, other_kind).evaluate(rule(time_rule())).metrics["faelle"] == 0.0


def test_backtest_is_reproducible(acfg):
    items = weekday_interventions()
    first = backtester(acfg, items).evaluate(rule(time_rule()))
    second = backtester(acfg, items).evaluate(rule(time_rule()))
    assert first == second


def test_missing_outside_sensor_makes_condition_candidate_untestable(acfg):
    candidate = rule({"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"},
                     conditions=[{"type": "outside_temp", "op": "below", "value": 8}])
    result = backtester(acfg, weekday_interventions()).evaluate(candidate)
    assert result.status == STATUS_UNTESTABLE and result.note and result.metrics == {}


def test_person_home_condition_without_person_data_is_untestable(acfg):
    candidate = rule({"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"},
                     conditions=[{"type": "person_home", "person": "person.unbekannt", "home": True}])
    result = backtester(acfg, weekday_interventions()).evaluate(candidate)
    assert result.status == STATUS_UNTESTABLE and result.note
