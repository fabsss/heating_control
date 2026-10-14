from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from analysis.context import ContextProvider
from analysis.features import (build_clusters, build_weekly, data_period, known_presets, room_name)
from analysis.interventions import load_interventions
from analysis.models import Intervention
from analysis.pseudonym import build_pseudonyms
from analysis.timeline import Timeline
from tests.analysis_fixture import ROOM, build_habit_db

T0 = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def habit(db, acfg):
    build_habit_db(db)
    cfg = replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=())
    pseudo = build_pseudonyms(db)
    ctx = ContextProvider.from_db(db, cfg, pseudo)
    start, end = data_period(db)
    return cfg, pseudo, ctx, load_interventions(db, cfg), start, end


def test_data_period_and_known_presets(db):
    assert data_period(db) is None
    build_habit_db(db)
    start, end = data_period(db)
    assert start < end
    assert known_presets(db) == {"eco", "comfort", "none"}


def test_room_name_falls_back_to_area_id(acfg):
    assert room_name(acfg, "office") == "Bastelzimmer"
    assert room_name(acfg, "unbekannt") == "unbekannt"


def test_weekly_cells_use_local_time_across_dst(habit):
    cfg, pseudo, ctx, interventions, start, end = habit
    data = build_weekly(interventions, ctx, cfg, pseudo, start, end)
    assert data["eingriffe_gesamt"] == 32
    cells = {(c["wochentag"], c["stunde"]): c for c in data["raeume"]["Wohnzimmer"]}
    monday = cells[("Montag", 17)]
    assert monday["eingriffe"] == 6                      # alle sechs Montage in derselben lokalen Stunde
    assert monday["haeufigste_aktion"] == {"type": "setpoint", "value": 21.0}
    assert monday["anteil_haeufigste"] == 1.0
    assert monday["personen_zuhause_anteil"] == {"person_2": 1.0}
    assert cells[("Samstag", 12)]["eingriffe"] == 2
    assert cells[("Samstag", 12)]["haeufigste_aktion"]["value"] == 19.0


def test_weekly_summarises_weekday_context(habit):
    cfg, pseudo, ctx, interventions, start, end = habit
    data = build_weekly(interventions, ctx, cfg, pseudo, start, end)
    share = data["wochentage"]["Montag"]["personen_zuhause_anteil"]["person_2"]
    assert 0.1 < share < 0.4                              # abends 5 von 24 Stunden zu Hause
    assert "wohnungszustand_anteile" not in data["wochentage"]["Montag"]   # Entität fehlt in der Fixture
    assert data["zeitraum"]["von"] == "2026-03-16"
    assert set(data["wochentage"]) == {"Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
                                       "Samstag", "Sonntag"}


def test_clusters_find_the_weekday_habit_with_context(habit):
    cfg, pseudo, ctx, interventions, start, end = habit
    data = build_clusters(interventions, ctx, cfg, pseudo, start, end)
    (cluster,) = data["cluster"]                          # die zwei Samstags-Eingriffe sind zu wenige
    assert cluster["raum"] == "Wohnzimmer"
    assert cluster["aktion"] == {"type": "setpoint", "value": 21.0}
    assert cluster["wochentage"] == "werktage"
    assert (cluster["anzahl"], cluster["wochen"]) == (30, 6)
    assert cluster["uhrzeit"] == {"von": "17:32", "median": "17:32", "bis": "17:32"}
    assert cluster["kontext"]["personen_zuhause_anteil"] == {"person_2": 1.0}
    assert cluster["kontext"]["aussentemperatur"] == {}   # kein Außensensor in der Fixture


def test_clusters_split_when_times_are_far_apart(acfg):
    cfg = replace(acfg, room_names={"r": "Raum"})
    items = [Intervention(T0 + timedelta(hours=h, minutes=m), "r", "climate.x", "setpoint", 21.0)
             for h, m in ((-2, 0), (-2, 10), (-2, 20), (10, 0), (10, 5), (10, 9))]
    ctx = ContextProvider.empty()
    data = build_clusters(items, ctx, cfg, ctx.pseudo, T0 - timedelta(days=1), T0 + timedelta(days=1))
    assert [c["anzahl"] for c in data["cluster"]] == [3, 3]


def test_snapshot_tolerates_missing_context():
    ctx = ContextProvider.empty()
    assert ctx.snapshot("r", T0) == {
        "persons_home": {}, "flat_state": None, "in_bed": None, "office_day": None,
        "windows_closed": None, "room_occupied": None, "outside_temp": None}
    assert not ctx.windows("r") and not ctx.person_timeline("person.x")


def test_snapshot_reads_windows_presence_and_temperature(db, acfg):
    ctx = ContextProvider(
        build_pseudonyms(db),
        windows={"r": [Timeline([(T0, "off")]), Timeline([(T0, "on")])]},
        presence={"r": [Timeline([(T0, "on")])]},
        outside_temp=Timeline([(T0, "7.5")]),
        flat_state=Timeline([(T0, "home")]),
    )
    later = T0 + timedelta(minutes=1)
    snap = ctx.snapshot("r", later)
    assert snap["windows_closed"] is False               # ein Fenster offen
    assert snap["room_occupied"] is True
    assert snap["outside_temp"] == 7.5 and snap["flat_state"] == "home"
    assert ctx.snapshot("anderer_raum", later)["windows_closed"] is None
