import json
from datetime import datetime, timedelta, timezone

from collector.db import Database
from collector.models import Change, EntityRule

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
CLIMATE = EntityRule(entity_id="climate.bt", kind="climate", domain="heating", attrs=("temperature", "preset_mode"))


def add(db, seconds, state, temperature=None, preset=None, user=None, parent=None, entity="climate.bt"):
    attrs = {} if temperature is None else {"temperature": temperature, "preset_mode": preset}
    db.insert_changes(
        [Change(T0 + timedelta(seconds=seconds), entity, state, attrs, f"c{seconds}", user, parent)], "live"
    )


def rows(db):
    return db.conn.execute(
        "SELECT ts_utc, changed_attrs, actor_class, actor_user_id, old, new FROM manual_actions ORDER BY ts_utc"
    ).fetchall()


def test_user_change_without_parent_is_manual(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 17, "none")
    add(db, 60, "heat", 21, "none", user="u1")
    (row,) = rows(db)
    assert row["changed_attrs"] == "temperature"
    assert (row["actor_class"], row["actor_user_id"]) == ("manual", "u1")
    assert json.loads(row["old"])["temperature"] == 17
    assert json.loads(row["new"])["temperature"] == 21


def test_change_with_parent_context_is_automation(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 21, "none")
    add(db, 60, "heat", 21, "comfort", parent="c0")
    (row,) = rows(db)
    assert (row["changed_attrs"], row["actor_class"]) == ("preset_mode", "automation")


def test_change_without_user_and_parent_is_unknown(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 17, "none")
    add(db, 60, "heat", 18, "none")
    (row,) = rows(db)
    assert row["actor_class"] == "unknown"


def test_user_and_parent_together_count_as_automation(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 17, "none")
    add(db, 60, "heat", 19, "none", user="u1", parent="cX")
    assert rows(db)[0]["actor_class"] == "automation"


def test_hvac_mode_change_is_reported(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 21, "comfort")
    add(db, 60, "off", 21, "comfort", user="u2")
    (row,) = rows(db)
    assert (row["changed_attrs"], row["actor_class"]) == ("hvac_mode", "manual")


def test_unavailable_transitions_are_not_reported(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 21, "none")
    add(db, 10, "unavailable")
    add(db, 20, "heat", 21, "none")
    assert rows(db) == []


def test_only_climate_entities_are_considered(db: Database):
    flat = EntityRule(entity_id="input_boolean.x", kind="flat", domain="shared")
    db.upsert_entity(flat, None, T0)
    add(db, 0, "off", entity="input_boolean.x")
    add(db, 10, "on", entity="input_boolean.x", user="u1")
    assert rows(db) == []


def test_room_follows_room_map_over_time(db: Database):
    db.upsert_entity(CLIMATE, "flur", T0)          # entities.room ist der aktuelle Raum
    db.add_history_room("buero", "climate.bt", "climate", None, "2026-01-01T10:00:30.000000+00:00")
    db.add_history_room("flur", "climate.bt", "climate", "2026-01-01T10:00:30.000000+00:00", None)
    add(db, 0, "heat", 17, "none")
    add(db, 20, "heat", 18, "none", user="u1")      # vor dem Umzug
    add(db, 60, "heat", 19, "none", user="u1")      # nach dem Umzug
    result = db.conn.execute("SELECT ts_utc, room FROM manual_actions ORDER BY ts_utc").fetchall()
    assert [r["room"] for r in result] == ["buero", "flur"]


def test_room_falls_back_to_entity_room_without_room_map(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 17, "none")
    add(db, 20, "heat", 18, "none", user="u1")
    (row,) = db.conn.execute("SELECT room FROM manual_actions").fetchall()
    assert row["room"] == "buero"


def test_entities_are_compared_separately(db: Database):
    other = EntityRule(entity_id="climate.other", kind="climate", domain="heating", attrs=("temperature", "preset_mode"))
    db.upsert_entity(CLIMATE, "buero", T0)
    db.upsert_entity(other, "bad", T0)
    add(db, 0, "heat", 17, "none")
    add(db, 5, "heat", 30, "none", entity="climate.other")
    add(db, 10, "heat", 17, "none", entity="climate.other")
    assert [r["changed_attrs"] for r in rows(db)] == ["temperature"]
