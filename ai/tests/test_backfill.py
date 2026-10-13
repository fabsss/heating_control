import sqlite3
from datetime import datetime, timezone

import pytest

from collector.backfill import (
    BackfillResult,
    UnsupportedSchema,
    HistoryMapEntry,
    backfill_ha_db,
    backfill_with_map,
    history_rules,
    load_history_map,
)
from collector.config import ConfigError
from collector.models import EntityInfo
from collector.rules import resolve_rule
from tests.ha_fixture import CTX, PARENT, USER_FABIAN, build_ha_db

CLIMATE_ATTRS = {"temperature": 17, "preset_mode": "none", "hvac_action": "idle",
                 "current_temperature": 20.0, "window_open": False, "heating_power": 0.02}


def row(entity_id, state, ts, attrs=None, **extra):
    return {"entity_id": entity_id, "state": state, "ts": ts, "attrs": attrs, **extra}


def rules_for(cfg, *entity_ids):
    return {e: resolve_rule(EntityInfo(e), cfg) for e in entity_ids}


def test_backfill_reads_states_converts_contexts_and_applies_filter(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("climate.bt", "heat", 1767261600.0, CLIMATE_ATTRS),
        row("climate.bt", "heat", 1767261660.0, CLIMATE_ATTRS),                         # unverändert -> verworfen
        row("climate.bt", "heat", 1767261720.0, {**CLIMATE_ATTRS, "temperature": 21},
            ctx=CTX, user=USER_FABIAN),
        row("climate.bt", "heat", 1767261780.0, {**CLIMATE_ATTRS, "temperature": 21, "preset_mode": "comfort"},
            ctx=CTX, parent=PARENT),
        row("sensor.not_selected", "5", 1767261600.0),
    ])
    result = backfill_ha_db(ha, db, rules_for(cfg, "climate.bt"), {"climate.bt": "buero"})
    assert isinstance(result, BackfillResult)
    assert (result.rows_read, result.rows_inserted) == (4, 3)
    rows = db.conn.execute(
        "SELECT state, attrs, context_id, context_user_id, context_parent_id, source "
        "FROM state_changes ORDER BY ts_utc").fetchall()
    assert len(rows) == 3
    assert rows[1]["context_id"] == CTX and rows[1]["context_user_id"] == USER_FABIAN
    assert rows[2]["context_parent_id"] == PARENT and rows[2]["context_user_id"] is None
    assert rows[0]["source"] == "backfill:home-assistant_v2.db"
    assert "heating_power" not in rows[0]["attrs"]
    assert db.conn.execute("SELECT room FROM entities WHERE entity_id='climate.bt'").fetchone()["room"] == "buero"
    run = db.conn.execute("SELECT source, rows, status FROM ingest_runs").fetchone()
    assert (run["source"], run["rows"], run["status"]) == ("backfill:home-assistant_v2.db", 3, "ok")


def test_backfill_skips_null_states_and_is_idempotent(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("input_boolean.x", "off", 1767261600.0),
        row("input_boolean.x", None, 1767261601.0),
        row("input_boolean.x", "on", 1767261700.0),
        row("input_boolean.empty_window", "on", 1900000000.0),
    ])
    rules = rules_for(cfg, "input_boolean.x", "input_boolean.never_seen")
    first = backfill_ha_db(ha, db, rules, {})
    second = backfill_ha_db(ha, db, rules, {})
    assert (first.rows_read, first.rows_inserted) == (3 - 1, 2)
    assert second.rows_inserted == 0
    assert db.count_changes("input_boolean.x") == 2
    assert db.count_changes("input_boolean.never_seen") == 0


def test_backfill_respects_since_and_until(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("input_boolean.x", "off", 1000.0),
        row("input_boolean.x", "on", 2000.0),
        row("input_boolean.x", "off", 3000.0),
    ])
    since = datetime.fromtimestamp(1500.0, tz=timezone.utc)
    until = datetime.fromtimestamp(2500.0, tz=timezone.utc)
    result = backfill_ha_db(ha, db, rules_for(cfg, "input_boolean.x"), {}, since=since, until=until)
    assert result.rows_read == 1
    assert [r["state"] for r in db.conn.execute("SELECT state FROM state_changes")] == ["on"]


def test_unsupported_schema_aborts_before_writing(cfg, db, tmp_path):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript("CREATE TABLE states(state_id INTEGER, entity_id TEXT, state TEXT);")
    conn.commit()
    conn.close()
    with pytest.raises(UnsupportedSchema, match="Schema"):
        backfill_ha_db(old, db, rules_for(cfg, "input_boolean.x"), {})
    assert db.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM ingest_runs").fetchone()[0] == 0


def test_history_only_entities_are_marked_inactive_with_last_timestamp(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("climate.trv_office", "heat", 1767261600.0, CLIMATE_ATTRS),
        row("climate.trv_office", "heat", 1767265200.0, {**CLIMATE_ATTRS, "temperature": 20}),
    ])
    rules = rules_for(cfg, "climate.trv_office")
    backfill_ha_db(ha, db, rules, {"climate.trv_office": "buero"}, history_only={"climate.trv_office"})
    entity = db.conn.execute("SELECT active, tracked_until, tracked_since FROM entities").fetchone()
    assert entity["active"] == 0
    assert entity["tracked_until"] == "2026-01-01T11:00:00.000000+00:00"
    assert entity["tracked_since"] == "2026-01-01T10:00:00.000000+00:00"


def test_backfilled_user_change_shows_up_in_manual_actions(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("climate.bt", "heat", 1767261600.0, CLIMATE_ATTRS),
        row("climate.bt", "heat", 1767261720.0, {**CLIMATE_ATTRS, "temperature": 21}, ctx=CTX, user=USER_FABIAN),
    ])
    backfill_ha_db(ha, db, rules_for(cfg, "climate.bt"), {"climate.bt": "buero"})
    (action,) = db.conn.execute(
        "SELECT actor_class, actor_user_id, changed_attrs FROM manual_actions").fetchall()
    assert (action["actor_class"], action["actor_user_id"], action["changed_attrs"]) == (
        "manual", USER_FABIAN, "temperature")


def test_history_map_loads_entries_and_rejects_unknown_keys(tmp_path):
    good = tmp_path / "h.yaml"
    good.write_text(
        "entities:\n"
        "  - {entity_id: climate.trv_office, room: buero, valid_to: '2025-12-01T00:00:00+00:00'}\n"
        "  - {entity_id: climate.trv_bathroom, room: bad, role: thermostat_trv}\n",
        encoding="utf-8")
    entries = load_history_map(good)
    assert entries[0] == HistoryMapEntry("climate.trv_office", "buero", valid_to="2025-12-01T00:00:00+00:00")
    assert entries[1].role == "thermostat_trv"
    bad = tmp_path / "b.yaml"
    bad.write_text("entities:\n  - {entity_id: climate.x, room: r, nope: 1}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="nope"):
        load_history_map(bad)


def test_history_rules_skip_labeled_entities(cfg):
    entries = [HistoryMapEntry("climate.trv_office", "buero"), HistoryMapEntry("climate.bt", "buero")]
    rules, rooms = history_rules(entries, cfg, labeled_ids={"climate.bt"})
    assert set(rules) == {"climate.trv_office"}
    assert rooms == {"climate.trv_office": "buero"}
    assert (rules["climate.trv_office"].kind, rules["climate.trv_office"].domain) == ("climate", "heating")


def test_backfill_with_map_combines_labeled_and_history_entities(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("climate.bt", "heat", 1767261600.0, CLIMATE_ATTRS),
        row("climate.trv_office", "heat", 1767261600.0, CLIMATE_ATTRS),
    ])
    entries = [HistoryMapEntry("climate.trv_office", "buero", valid_to="2026-01-02T00:00:00+00:00")]
    result = backfill_with_map(cfg, db, [EntityInfo("climate.bt", None, "buero")], entries, ha)
    assert result.rows_inserted == 2
    states = {r["entity_id"]: r["active"] for r in db.conn.execute("SELECT entity_id, active FROM entities")}
    assert states == {"climate.bt": 1, "climate.trv_office": 0}
    room = db.conn.execute("SELECT room, valid_to FROM room_map WHERE entity_id='climate.trv_office'").fetchone()
    assert (room["room"], room["valid_to"]) == ("buero", "2026-01-02T00:00:00+00:00")
