from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from analysis.pseudonym import Pseudonyms
from analysis.signals import SignalLibrary, build_signal
from analysis.timeline import Timeline
from collector.models import EntityRule
from tests.analysis_fixture import ROOM, build_habit_db

T0 = datetime(2026, 1, 5, 6, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def timeline(*states, step_min=30):
    return Timeline([(T0 + timedelta(minutes=step_min * index), state) for index, state in enumerate(states)])


def pseudo(*persons):
    return Pseudonyms(tuple(persons), {}, {})


def build(acfg, entity_id, kind, states, *, room=None, persons=()):
    return build_signal("sig_01", entity_id, kind, room, timeline(*states), acfg, pseudo(*persons))


def test_person_zones_collapse_to_home_and_away(acfg):
    signal = build(acfg, "person.a", "person", ["home", "Work", "not_home", "unavailable", "home"],
                   persons=("person.a",))
    assert signal.info.value_kind == "categorical" and signal.info.values == ("away", "home")
    assert [state for _, state in signal.timeline.points()] == ["home", "away", "away", None, "home"]


def test_binary_states_are_categorical(acfg):
    states = ["on", "off"] * 10
    signal = build(acfg, "binary_sensor.x", "flat", states)
    assert signal.info.value_kind == "categorical" and signal.info.values == ("off", "on")


def test_rare_values_become_other(acfg):
    states = ["a", "b"] * 10 + ["selten"]
    signal = build(acfg, "input_select.x", "flat", states)
    assert signal.info.values == ("a", "b")
    assert signal.timeline.points()[-1][1] == "other"


def test_numbers_are_numeric_with_terciles(acfg):
    signal = build(acfg, "sensor.t", "env", [str(value) for value in range(30)])
    assert signal.info.value_kind == "numeric"
    assert (signal.info.low, signal.info.high) == (0.0, 29.0)
    assert signal.info.terciles == (10.0, 20.0)


def test_constant_numbers_are_unusable(acfg):
    assert build(acfg, "sensor.t", "env", ["5"] * 12).info.value_kind == "none"


def test_iso_timestamps_are_timestamp_signals(acfg):
    states = [f"2026-01-0{day}T06:30:00+00:00" for day in range(1, 7)]
    assert build(acfg, "sensor.alarm", "env", states).info.value_kind == "timestamp"


def test_free_text_is_not_a_signal(acfg):
    assert build(acfg, "sensor.text", "env", [f"text {n}" for n in range(60)]).info.value_kind == "none"


def test_signal_with_one_known_state_is_unusable(acfg):
    assert build(acfg, "sensor.x", "env", ["unavailable", "on", "unknown"]).info.value_kind == "none"


def test_description_never_comes_from_the_entity_id(acfg):
    cfg = replace(acfg, room_names={ROOM: "Wohnzimmer"}, signal_labels={"sensor.alarm": "Weckzeit"},
                  context={**acfg.context, "in_bed": "input_boolean.bett"})
    contact = build_signal("sig_01", "binary_sensor.fabians_fenster", "contact", ROOM, timeline("on", "off"), cfg, pseudo())
    assert contact.info.description == "Kontakt, Wohnzimmer" and "fabian" not in contact.info.description.lower()
    assert build_signal("sig_02", "sensor.alarm", "env", None, timeline("a", "b"), cfg, pseudo()).info.description == "Weckzeit"
    assert build_signal("sig_03", "input_boolean.bett", "flat", None, timeline("on", "off"), cfg, pseudo()).info.description == "Jemand im Bett"
    person = build_signal("sig_04", "person.svenja", "person", None, timeline("home", "away"), cfg, pseudo("person.fabian", "person.svenja"))
    assert person.info.description == "Anwesenheit person_2 (home oder away)"


@pytest.fixture
def cfg(acfg):
    return replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=())


def test_library_builds_signals_from_the_database_and_skips_thermostats(db, cfg):
    build_habit_db(db)
    from analysis.pseudonym import build_pseudonyms
    library = SignalLibrary.from_db(db, cfg, build_pseudonyms(db))
    by_entity = {signal.entity_id: signal for signal in library.all()}
    assert "climate.bt" not in by_entity
    assert by_entity["person.svenja"].info.value_kind == "categorical"
    assert by_entity["person.fabian"].info.value_kind == "none"           # keine Zustandswechsel aufgezeichnet
    assert [signal.entity_id for signal in library.usable()] == ["person.svenja"]
    assert library.get(by_entity["person.svenja"].handle) is by_entity["person.svenja"]


def test_handles_stay_stable_when_entities_are_added(db, cfg):
    from analysis.pseudonym import build_pseudonyms
    build_habit_db(db)
    first = {s.entity_id: s.handle for s in SignalLibrary.from_db(db, cfg, build_pseudonyms(db)).all()}
    db.upsert_entity(EntityRule("binary_sensor.a_new", "flat", "shared"), None, NOW)     # sortiert vor person.*
    second = {s.entity_id: s.handle for s in SignalLibrary.from_db(db, cfg, build_pseudonyms(db)).all()}
    assert all(second[entity] == handle for entity, handle in first.items())
    assert second["binary_sensor.a_new"] == "sig_03"


def test_catalog_lists_only_usable_signals_with_their_values(db, cfg):
    from analysis.pseudonym import build_pseudonyms
    build_habit_db(db)
    catalog = SignalLibrary.from_db(db, cfg, build_pseudonyms(db)).catalog()
    assert catalog == [{"id": "sig_02", "beschreibung": "Anwesenheit person_2 (home oder away)",
                        "art": "Kategorie", "werte": ["away", "home"]}]


def test_leak_check_flags_names_in_labels(db, cfg):
    from analysis.pseudonym import build_pseudonyms
    build_habit_db(db)
    bad = replace(cfg, signal_labels={"person.svenja": "Handy von Svenja"})
    pseudo_ = build_pseudonyms(db)
    assert SignalLibrary.from_db(db, bad, pseudo_).leaks(pseudo_) == ["svenja"]
    assert SignalLibrary.from_db(db, cfg, pseudo_).leaks(pseudo_) == []


def test_signal_rows_are_persisted_with_description(db, cfg):
    from analysis.pseudonym import build_pseudonyms
    from analysis.store import Store
    build_habit_db(db)
    SignalLibrary.from_db(db, cfg, build_pseudonyms(db))
    rows = {row["entity_id"]: row for row in Store(db).list_signals()}
    assert rows["person.svenja"]["value_kind"] == "categorical"
    assert rows["person.svenja"]["description"].startswith("Anwesenheit person_")
    assert Store(db).signal_descriptions()[rows["person.svenja"]["handle"]] == rows["person.svenja"]["description"]
