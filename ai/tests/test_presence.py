from dataclasses import replace
from datetime import timedelta

import pytest

from analysis.presence import analyze, render
from collector.models import Change
from tests.analysis_fixture import FIRST_MONDAY, ROOM, build_habit_db, local_utc


@pytest.fixture
def cfg(acfg):
    return replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=())


@pytest.fixture
def report(db, cfg):
    build_habit_db(db)
    return analyze(db, cfg)


def row(report, klass):
    (found,) = [r for r in report.rows if r.klass == klass]
    return found


def test_interventions_cluster_right_after_a_long_absence_and_not_in_the_control(report):
    arrival = row(report, "≥180")
    assert arrival.person == "Svenja" and arrival.count == 30        # 10 Stunden weg (07:00 bis 17:20)
    assert arrival.hits[0] == 30                                      # Eingriff 12 Minuten nach jeder Ankunft
    assert all(arrival.hits[start] == 0 for start in arrival.hits if start != 0)
    assert arrival.control_count > 0 and sum(arrival.control_hits.values()) == 0


def test_bins_run_from_two_hours_before_to_two_hours_after(report):
    assert sorted(row(report, "≥180").hits) == [-120, -90, -60, -30, 0, 30, 60, 90]


def test_short_absences_are_a_separate_class(db, cfg):
    build_habit_db(db)
    day = FIRST_MONDAY
    db.insert_changes([Change(local_utc(day, "20:00"), "person.svenja", "not_home"),
                       Change(local_utc(day, "20:20"), "person.svenja", "home")], "live")     # Gassi: 20 Minuten
    result = analyze(db, cfg)
    assert row(result, "<45").count == 1 and row(result, "≥180").count == 30
    assert row(result, "<45").hits[0] == 0


def test_medium_absence_class_and_custom_edges(db, cfg):
    build_habit_db(db)
    day = FIRST_MONDAY
    db.insert_changes([Change(local_utc(day, "20:00"), "person.svenja", "not_home"),
                       Change(local_utc(day, "21:30"), "person.svenja", "home")], "live")     # 90 Minuten
    assert row(analyze(db, cfg), "45-180").count == 1
    assert row(analyze(db, cfg, edges=(30, 60)), "≥60").count == 31        # 30 Ankünfte lang + die 90-Minuten-Rückkehr


def test_unavailable_before_the_arrival_is_not_an_arrival(db, cfg):
    build_habit_db(db)
    day = FIRST_MONDAY + timedelta(days=5)
    db.insert_changes([Change(local_utc(day, "12:20"), "person.svenja", "unavailable"),
                       Change(local_utc(day, "12:30"), "person.svenja", "home")], "live")
    assert sum(r.count for r in analyze(db, cfg).rows) == 30


def test_arrival_before_an_intervention_is_found_in_the_negative_bins(db, cfg):
    build_habit_db(db)
    day = FIRST_MONDAY + timedelta(days=5)                       # Samstag: Svenja kommt um 12:30 heim
    db.insert_changes([Change(local_utc(day, "12:30"), "person.svenja", "home")], "live")
    arrival = row(analyze(db, cfg), "≥180")
    assert arrival.count == 31 and arrival.hits[-30] == 1         # Eingriff 12:00, 30 Minuten vor der Ankunft


def test_persons_without_changes_are_left_out(report):
    assert {r.person for r in report.rows} == {"Svenja"}


def test_delay_buckets_use_arrivals_after_the_first_edge(db, cfg):
    build_habit_db(db)
    day = FIRST_MONDAY
    db.insert_changes([Change(local_utc(day, "20:00"), "person.svenja", "not_home"),
                       Change(local_utc(day, "20:20"), "person.svenja", "home")], "live")     # zählt nicht
    result = analyze(db, cfg)
    assert result.delays["≤15 min"][0] == 30 and result.delays["später/nie"][0] == 2
    assert sum(count for count, _ in result.delays.values()) == 32
    assert result.grid_total > 0


def test_render_contains_sections_and_numbers(report):
    text = render(report)
    for heading in ("Ankünfte", "Zeit seit der letzten Ankunft", "Svenja ≥180"):
        assert heading in text
    assert "100%" in text and "-30 bis 0" in text


def test_report_without_interventions_says_so(db, acfg):
    assert "Keine manuellen Eingriffe" in render(analyze(db, acfg))
