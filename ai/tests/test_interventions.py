from dataclasses import replace
from datetime import datetime, timedelta, timezone

from analysis.config import Exclusion
from analysis.interventions import excluded, load_interventions
from collector.models import Change, EntityRule

T0 = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
CLIMATE = EntityRule("climate.bt", "climate", "heating", attrs=("temperature", "preset_mode"))


def put(db, seconds, state="heat", temperature=17, preset="eco", user=None, parent=None, ctx=None):
    db.insert_changes([Change(T0 + timedelta(seconds=seconds), "climate.bt", state,
                              {"temperature": temperature, "preset_mode": preset},
                              ctx or f"c{seconds}", user, parent)], "live")


def prepared(db, room="r_living"):
    db.upsert_entity(CLIMATE, room, T0)
    put(db, 0)                                       # Ausgangszustand


def test_setpoint_change_by_user_is_a_setpoint_intervention(db, acfg):
    prepared(db)
    put(db, 60, temperature=21, preset="comfort", user="u1")   # beides ändert sich: Sollwert gewinnt
    (item,) = load_interventions(db, acfg)
    assert (item.kind, item.value, item.room, item.entity_id, item.user_id) == (
        "setpoint", 21.0, "r_living", "climate.bt", "u1")
    assert item.ts == T0 + timedelta(seconds=60)


def test_preset_only_change_is_a_preset_intervention(db, acfg):
    prepared(db)
    put(db, 60, temperature=17, preset="comfort", user="u1")
    (item,) = load_interventions(db, acfg)
    assert (item.kind, item.value) == ("preset", "comfort")


def test_hvac_mode_only_change_is_skipped(db, acfg):
    prepared(db)
    put(db, 60, state="off", user="u1")
    assert load_interventions(db, acfg) == []


def test_automation_and_unknown_are_not_interventions(db, acfg):
    prepared(db)
    put(db, 60, temperature=21, parent="p1", user="u1")
    put(db, 120, temperature=22)
    assert load_interventions(db, acfg) == []


def test_rows_within_merge_window_become_one_intervention(db, acfg):
    prepared(db)
    put(db, 100, temperature=18, user="u1")
    put(db, 130, temperature=19, user="u1")
    put(db, 170, temperature=20, user="u1")          # 40 s nach der vorigen Zeile: gleiche Kette
    put(db, 400, temperature=22, user="u1")          # neuer Eingriff
    items = load_interventions(db, acfg)
    assert [(i.ts, i.value) for i in items] == [
        (T0 + timedelta(seconds=100), 20.0),
        (T0 + timedelta(seconds=400), 22.0),
    ]


def test_entity_without_room_is_skipped(db, acfg):
    prepared(db, room=None)
    put(db, 60, temperature=21, user="u1")
    assert load_interventions(db, acfg) == []


def test_period_filter_is_half_open(db, acfg):
    prepared(db)
    put(db, 100, temperature=18, user="u1")
    put(db, 500, temperature=21, user="u1")
    start, end = T0 + timedelta(seconds=100), T0 + timedelta(seconds=500)
    items = load_interventions(db, acfg, start=start, end=end)
    assert [i.value for i in items] == [18.0]


def test_exclusion_drops_interventions_of_that_room_only(db, acfg):
    prepared(db)
    put(db, 60, temperature=21, user="u1")
    cfg = replace(acfg, exclusions=(Exclusion("r_living", T0, T0 + timedelta(hours=1)),))
    assert load_interventions(db, cfg) == []
    other = replace(acfg, exclusions=(Exclusion("r_anders", T0, None),))
    assert len(load_interventions(db, other)) == 1


def test_excluded_handles_open_end(acfg):
    cfg = replace(acfg, exclusions=(Exclusion("r", T0, None),))
    assert excluded(cfg, "r", T0 + timedelta(days=900))
    assert not excluded(cfg, "r", T0 - timedelta(seconds=1))
    assert not excluded(cfg, "andere", T0)
