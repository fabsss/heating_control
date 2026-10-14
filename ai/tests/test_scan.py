import random
from dataclasses import replace
from datetime import timedelta
from math import comb

import pytest

from analysis.features import data_period
from analysis.interventions import load_interventions
from analysis.pseudonym import build_pseudonyms
from analysis.scan import benjamini_hochberg, fisher_greater, run_scan
from analysis.signals import SignalLibrary
from tests.analysis_fixture import ROOM, local_utc
from tests.scan_fixture import after_arrival, build_scan_db


def exact_fisher(hits, events, control_hits, control_events):
    total, successes = events + control_events, hits + control_hits
    return sum(comb(successes, k) * comb(total - successes, events - k)
               for k in range(hits, min(events, successes) + 1)) / comb(total, events)


@pytest.mark.parametrize("args", [(5, 10, 0, 40), (8, 12, 3, 30), (1, 20, 1, 100), (4, 4, 0, 4)])
def test_fisher_matches_the_exact_hypergeometric_tail(args):
    assert fisher_greater(*args) == pytest.approx(exact_fisher(*args), rel=1e-9)


def test_fisher_without_hits_or_controls_is_one():
    assert fisher_greater(0, 10, 3, 30) == 1.0 and fisher_greater(3, 10, 0, 0) == 1.0


def test_benjamini_hochberg_example():
    assert benjamini_hochberg([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert benjamini_hochberg([]) == []


@pytest.fixture
def cfg(acfg):
    return replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=())


def scan(db, cfg, **overrides):
    cfg = replace(cfg, scan=replace(cfg.scan, **overrides)) if overrides else cfg
    pseudo = build_pseudonyms(db)
    library = SignalLibrary.from_db(db, cfg, pseudo)
    start, end = data_period(db)
    return run_scan(cfg, library, load_interventions(db, cfg), start, end), library


def test_the_hidden_habit_is_the_strongest_association(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [12]))
    found, library = scan(db, cfg)
    top = found[0]
    assert library.get(top.signal).entity_id == "person.svenja"
    assert (top.event, top.value) == ("to_state", "home")
    assert (top.win_start, top.win_end) == (0, 30)
    assert top.hits == top.events == 30 and top.control_hits == 0 and top.q < 0.01 and top.lift > 10
    assert top.typical_action == "Sollwert 21 °C (30 von 30)"


def test_leaving_is_not_associated(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [12]))
    found, _ = scan(db, cfg)
    assert [item for item in found if item.value == "away"] == []


def test_results_exist_for_the_room_and_for_all_rooms(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [12]))
    found, _ = scan(db, cfg)
    assert {item.room for item in found if item.value == "home" and item.prev_class == "alle"} == {None, ROOM}


def test_small_rooms_are_only_covered_by_all_rooms(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [12]))
    found, _ = scan(db, cfg, min_room_interventions=500)
    assert {item.room for item in found} == {None}


def test_duration_classes_separate_long_from_short_absences(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [12]))
    found, _ = scan(db, cfg)
    classes = {item.prev_class for item in found if item.value == "home"}
    assert "≥180" in classes and "alle" in classes and "<45" not in classes


def test_adjacent_bins_are_merged_into_one_window(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [10] if n % 2 == 0 else [40]))
    found, _ = scan(db, cfg)
    merged = [item for item in found if item.value == "home" and item.prev_class == "alle" and item.room is None]
    assert [(item.win_start, item.win_end) for item in merged] == [(0, 60)]
    assert merged[0].hits == merged[0].events


def test_interventions_before_the_event_are_found_in_negative_bins(db, cfg):
    build_scan_db(db, after_arrival(lambda n: [-45]))
    found, _ = scan(db, cfg)
    top = [item for item in found if item.value == "home" and item.room is None][0]
    assert (top.win_start, top.win_end) == (-60, -30)


def test_random_interventions_produce_no_significant_associations(db, cfg):
    generator = random.Random(11)

    def scattered(n, day):
        return [local_utc(day, f"{generator.randint(9, 22):02d}:{generator.randint(0, 59):02d}")
                for _ in range(generator.choice([1, 2]))]

    build_scan_db(db, scattered)
    found, _ = scan(db, cfg)
    assert found == []


def test_alarm_signal_is_found_without_special_handling(db, cfg):
    build_scan_db(db, lambda n, day: [local_utc(day, "06:30") + timedelta(minutes=10)], alarm_time="06:30")
    found, library = scan(db, cfg)
    alarm = [item for item in found if item.event == "alarm_time"]
    assert alarm and library.get(alarm[0].signal).entity_id == "sensor.next_alarm"
    assert (alarm[0].win_start, alarm[0].win_end) == (0, 30)
    assert alarm[0].hits >= alarm[0].events - 1          # am ersten Tag fehlt die Vorgeschichte für einen Eingriff


def test_scan_without_interventions_returns_nothing(db, cfg):
    build_scan_db(db, lambda n, day: [])
    found, _ = scan(db, cfg)
    assert found == []
