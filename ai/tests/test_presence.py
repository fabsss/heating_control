from dataclasses import replace

import pytest

from analysis.interventions import load_interventions
from analysis.presence import analyze, render
from tests.analysis_fixture import ROOM, build_habit_db


@pytest.fixture
def report(db, acfg):
    build_habit_db(db)
    cfg = replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=())
    return analyze(db, cfg, windows=(15, 60)), cfg


def test_arrivals_are_followed_by_interventions_far_more_often_than_control(report):
    result, _ = report
    (row,) = [r for r in result.arrivals if r.event == "kommt"]
    assert row.person == "Svenja" and row.count == 30
    assert row.hits == {15: 30, 60: 30}                     # Eingriff 12 Minuten nach jeder Ankunft
    assert row.control_count > 0 and row.control_hits == {15: 0, 60: 0}


def test_persons_without_changes_are_left_out(report):
    result, _ = report
    assert {r.person for r in result.arrivals} == {"Svenja"}
    assert {r.person for r in result.presence} == {"Svenja"}


def test_presence_at_interventions_is_compared_with_time_at_home(report):
    result, _ = report
    (row,) = result.presence
    assert (row.at_interventions, row.interventions) == (30, 32)      # 2 Samstags-Eingriffe ohne Svenja
    assert row.baseline_total > 0 and row.baseline_home / row.baseline_total < 0.5


def test_delay_buckets_for_interventions_and_for_the_time_grid(report):
    result, _ = report
    assert result.delays["≤15 min"][0] == 30 and result.delays["später/nie"][0] == 2
    assert sum(count for count, _ in result.delays.values()) == 32
    assert result.grid_total > 0


def test_render_contains_all_sections_and_the_numbers(report):
    result, _ = report
    text = render(result)
    for heading in ("Ankünfte", "Anwesenheit bei Eingriffen", "Zeit seit der letzten Ankunft"):
        assert heading in text
    assert "Svenja" in text and "100%" in text


def test_report_without_interventions_says_so(db, acfg):
    result = analyze(db, acfg)
    assert "Keine manuellen Eingriffe" in render(result)
