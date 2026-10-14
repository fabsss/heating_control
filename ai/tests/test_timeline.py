from datetime import datetime, timedelta, timezone

from analysis.timeline import Timeline, load_timeline
from collector.models import Change, EntityRule

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def timeline(*items) -> Timeline:
    return Timeline([(T0 + timedelta(minutes=minutes), state) for minutes, state in items])


def test_value_at_returns_last_state_not_after_t():
    tl = timeline((0, "off"), (10, "on"), (20, "off"))
    assert tl.value_at(T0 - timedelta(minutes=1)) is None
    assert tl.value_at(T0) == "off"
    assert tl.value_at(T0 + timedelta(minutes=10)) == "on"
    assert tl.value_at(T0 + timedelta(minutes=19)) == "on"
    assert tl.value_at(T0 + timedelta(hours=5)) == "off"


def test_transitions_only_where_state_changes():
    tl = timeline((0, "off"), (5, "off"), (10, "on"), (20, "off"))
    assert tl.transitions() == [
        (T0 + timedelta(minutes=10), "off", "on"),
        (T0 + timedelta(minutes=20), "on", "off"),
    ]


def test_numeric_at_ignores_non_numbers():
    tl = timeline((0, "18.5"), (10, "unavailable"))
    assert tl.numeric_at(T0) == 18.5
    assert tl.numeric_at(T0 + timedelta(minutes=10)) is None
    assert tl.numeric_at(T0 - timedelta(minutes=1)) is None


def test_empty_timeline_is_falsy_and_safe():
    tl = Timeline()
    assert not tl
    assert tl.value_at(T0) is None and tl.numeric_at(T0) is None
    assert tl.transitions() == [] and tl.states() == set()


def test_unsorted_points_are_sorted():
    tl = timeline((10, "on"), (0, "off"))
    assert tl.value_at(T0 + timedelta(minutes=5)) == "off"


def test_load_timeline_reads_state_changes(db):
    db.upsert_entity(EntityRule("input_boolean.x", "flat", "shared"), None, T0)
    db.insert_changes([Change(T0, "input_boolean.x", "off"),
                       Change(T0 + timedelta(minutes=5), "input_boolean.x", "on")], "live")
    tl = load_timeline(db, "input_boolean.x")
    assert tl.states() == {"off", "on"}
    assert tl.value_at(T0 + timedelta(minutes=6)) == "on"
    assert not load_timeline(db, "input_boolean.gibt_es_nicht")
