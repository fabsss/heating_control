import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from collector.db import Database
from collector.models import Change, EntityRule
from collector.pipeline import Pipeline

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
FLAT = EntityRule(entity_id="input_boolean.x", kind="flat", domain="shared")
ROOMS = EntityRule(entity_id="sensor.room", kind="presence", domain="shared", debounce_s=60)


def flat(seconds, state, source_entity="input_boolean.x") -> Change:
    return Change(T0 + timedelta(seconds=seconds), source_entity, state)


def make(db: Database, *rules: EntityRule) -> Pipeline:
    pipeline = Pipeline(db)
    for rule in rules:
        db.upsert_entity(rule, None, T0)
    pipeline.set_rules({rule.entity_id: rule for rule in rules})
    return pipeline


def test_unknown_entities_are_ignored(db):
    pipeline = make(db, FLAT)
    pipeline.handle(flat(0, "on", "light.nobody_listens"))
    assert pipeline.buffered == 0


def test_handle_buffers_and_flush_writes_with_source(db):
    pipeline = make(db, FLAT)
    pipeline.handle(flat(0, "off"))
    pipeline.handle(flat(1, "off"))
    pipeline.handle(flat(2, "on"), source="gap:history_api")
    assert pipeline.buffered == 2
    assert pipeline.flush() == 2
    assert pipeline.buffered == 0
    rows = db.conn.execute("SELECT state, source FROM state_changes ORDER BY ts_utc").fetchall()
    assert [(r["state"], r["source"]) for r in rows] == [("off", "live"), ("on", "gap:history_api")]


def test_flush_of_empty_buffer_returns_zero(db):
    assert make(db, FLAT).flush() == 0


def test_filter_is_seeded_from_stored_data(db):
    db.upsert_entity(FLAT, None, T0)
    db.insert_changes([flat(0, "on")], "live")
    pipeline = Pipeline(db)
    pipeline.set_rules({FLAT.entity_id: FLAT})
    pipeline.handle(flat(60, "on"))
    assert pipeline.buffered == 0
    pipeline.handle(flat(120, "off"))
    assert pipeline.buffered == 1


def test_set_rules_keeps_state_for_unchanged_rules_and_drops_removed(db):
    pipeline = make(db, FLAT, ROOMS)
    pipeline.handle(flat(0, "on"))
    pipeline.set_rules({FLAT.entity_id: FLAT})            # ROOMS entfernt, FLAT unverändert
    pipeline.handle(flat(5, "on"))                        # gleicher Zustand -> weiterhin verworfen
    pipeline.handle(Change(T0, "sensor.room", "Büro"))     # entfernt -> ignoriert
    assert pipeline.buffered == 1


def test_tick_confirms_debounced_values(db):
    pipeline = make(db, ROOMS)
    pipeline.handle(Change(T0, "sensor.room", "Büro"))
    assert pipeline.buffered == 0
    pipeline.tick(T0 + timedelta(seconds=61))
    assert pipeline.buffered == 1


def test_flush_keeps_buffer_when_write_fails(db, monkeypatch):
    pipeline = make(db, FLAT)
    pipeline.handle(flat(0, "on"))
    original = db.insert_changes

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "insert_changes", broken)
    with pytest.raises(sqlite3.OperationalError):
        pipeline.flush()
    assert pipeline.buffered == 1
    monkeypatch.setattr(db, "insert_changes", original)
    assert pipeline.flush() == 1
    assert pipeline.buffered == 0
