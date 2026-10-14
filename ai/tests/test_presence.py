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


def row(report, event):
    (found,) = [r for r in report.rows if r.event == event]
    return found


def test_interventions_cluster_right_after_the_arrival_and_not_in_the_control(report):
    arrival = row(report, "kommt")
    assert arrival.person == "Svenja" and arrival.count == 30
    assert arrival.hits[0] == 30                              # Eingriff 12 Minuten nach jeder Ankunft
    assert all(arrival.hits[start] == 0 for start in arrival.hits if start != 0)
    assert arrival.control_count > 0 and sum(arrival.control_hits.values()) == 0


def test_leaves_show_no_effect(report):
    leave = row(report, "geht")
    assert leave.count == 30 and sum(leave.hits.values()) == 0


def test_bins_run_from_two_hours_before_to_two_hours_after(report):
    assert sorted(row(report, "kommt").hits) == [-120, -90, -60, -30, 0, 30, 60, 90]


def test_persons_without_changes_are_left_out(report):
    assert {r.person for r in report.rows} == {"Svenja"}


def test_flickering_presence_is_not_counted_as_an_arrival(db, cfg):
    build_habit_db(db)
    day = FIRST_MONDAY
    db.insert_changes([Change(local_utc(day, "20:00"), "person.svenja", "not_home"),
                       Change(local_utc(day, "20:03"), "person.svenja", "home")], "live")
    arrival = row(analyze(db, cfg), "kommt")
    assert arrival.raw_count == 31 and arrival.count == 30        # die Rückkehr nach 3 Minuten fällt weg
    assert row(analyze(db, cfg, min_state_min=0), "kommt").count == 31


def test_arrival_before_an_intervention_is_found_in_the_negative_bins(db, cfg):
    build_habit_db(db)
    day = FIRST_MONDAY + timedelta(days=5)                       # Samstag: Svenja kommt um 12:30 heim
    db.insert_changes([Change(local_utc(day, "12:30"), "person.svenja", "home")], "live")
    arrival = [r for r in analyze(db, cfg).rows if r.event == "kommt"][0]
    assert arrival.count == 31 and arrival.hits[-30] == 1         # Eingriff 12:00, 30 Minuten vor der Ankunft


def test_delay_buckets_for_interventions_and_for_the_time_grid(report):
    assert report.delays["≤15 min"][0] == 30 and report.delays["später/nie"][0] == 2
    assert sum(count for count, _ in report.delays.values()) == 32
    assert report.grid_total > 0


def test_render_contains_sections_and_numbers(report):
    text = render(report)
    for heading in ("Ankünfte und Abgänge", "Zeit seit der letzten Ankunft", "Svenja kommt"):
        assert heading in text
    assert "100%" in text and "-30 bis 0" in text


def test_report_without_interventions_says_so(db, acfg):
    assert "Keine manuellen Eingriffe" in render(analyze(db, acfg))
