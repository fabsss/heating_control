import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from collector import live
from collector.hass_ws import HassAuthError, HassClient, HassConnectionError
from collector.live import Collector, change_from_event, history_to_changes, seconds_until
from collector.registry import LabelNotFound
from tests.fake_hass import set_registry

HISTORY = "history/history_during_period"


def entity_entry(entity_id, labels=("ki_db_sync",), area_id="buero", device_class=None):
    return {"entity_id": entity_id, "labels": list(labels), "area_id": area_id, "device_id": None,
            "device_class": device_class, "original_device_class": None, "disabled_by": None}


def state_event(entity_id, state, stamp, attrs=None, user=None, parent=None, ctx="01CTX"):
    return {"event_type": "state_changed", "data": {
        "entity_id": entity_id, "old_state": None,
        "new_state": {"entity_id": entity_id, "state": state, "attributes": attrs or {},
                      "last_updated": stamp,
                      "context": {"id": ctx, "parent_id": parent, "user_id": user}}}}


def prepare(fake, entities):
    set_registry(fake, entities)
    fake.responses["get_states"] = [
        {"entity_id": "person.fabian", "attributes": {"user_id": "u-fabian", "friendly_name": "Fabian"}}
    ]
    fake.responses[HISTORY] = {}


def make_collector(cfg, db, fake, **kwargs) -> Collector:
    return Collector(cfg, db, lambda: HassClient(fake.url, "secret"), **kwargs)


# --- reine Funktionen ------------------------------------------------------

def test_change_from_event_parses_context_and_z_timestamp():
    event = state_event("climate.bt", "heat", "2026-01-01T10:00:00Z", {"temperature": 21},
                        user="u1", parent="p1", ctx="c1")
    change = change_from_event(event)
    assert change.entity_id == "climate.bt"
    assert change.ts == datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    assert (change.context_id, change.context_user_id, change.context_parent_id) == ("c1", "u1", "p1")
    assert change.attrs == {"temperature": 21}


def test_change_from_event_ignores_removed_entity():
    assert change_from_event({"data": {"entity_id": "x", "new_state": None}}) is None
    assert change_from_event({}) is None


def test_history_to_changes_converts_epoch_and_skips_incomplete_rows():
    changes = history_to_changes("input_boolean.x", [
        {"s": "on", "a": {"k": 1}, "lu": 1767261600.0},
        {"s": "off", "lc": 1767265200.0},
        {"a": {}},
        {"s": None, "lu": 1767265300.0},
    ])
    assert [(c.state, c.ts.isoformat()) for c in changes] == [
        ("on", "2026-01-01T10:00:00+00:00"),
        ("off", "2026-01-01T11:00:00+00:00"),
    ]
    assert changes[0].attrs == {"k": 1}


def test_seconds_until_waits_for_next_local_time():
    now = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)          # 03:00 in Berlin
    assert seconds_until("03:30", "Europe/Berlin", now) == 1800
    later = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)        # 04:00 in Berlin -> nächster Tag
    assert seconds_until("03:30", "Europe/Berlin", later) == 84600


# --- Registry-Abgleich -------------------------------------------------------

async def test_sync_registry_creates_entities_rules_rooms_and_users(cfg, db, fake_hass):
    prepare(fake_hass, [
        entity_entry("climate.better_thermostat_office", area_id="buero"),
        entity_entry("input_boolean.someone_in_bed", area_id=None),
        entity_entry("sensor.unlabeled", labels=()),
    ])
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    collector = make_collector(cfg, db, fake_hass)
    try:
        await collector.sync_registry(client)
    finally:
        await client.close()
    entities = {r["entity_id"]: r for r in db.conn.execute("SELECT * FROM entities")}
    assert set(entities) == {"climate.better_thermostat_office", "input_boolean.someone_in_bed"}
    assert entities["climate.better_thermostat_office"]["domain"] == "heating"
    assert set(collector.pipeline.rules) == set(entities)
    assert [r["room"] for r in db.conn.execute("SELECT room FROM room_map")] == ["buero"]
    assert db.conn.execute("SELECT name FROM users WHERE user_id='u-fabian'").fetchone()["name"] == "Fabian"


async def test_sync_registry_raises_when_label_is_missing_on_first_sync(cfg, db, fake_hass):
    set_registry(fake_hass, [entity_entry("sensor.x")], labels=[])
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    try:
        with pytest.raises(LabelNotFound):
            await make_collector(cfg, db, fake_hass).sync_registry(client)
    finally:
        await client.close()


async def test_sync_registry_keeps_running_when_label_vanishes_later(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    collector = make_collector(cfg, db, fake_hass)
    try:
        await collector.sync_registry(client)
        set_registry(fake_hass, [entity_entry("input_boolean.someone_in_bed")], labels=[])
        await collector.sync_registry(client)          # kein Fehler, bisherige Entitäten bleiben
    finally:
        await client.close()
    assert db.active_entity_ids() == {"input_boolean.someone_in_bed"}
    assert set(collector.pipeline.rules) == {"input_boolean.someone_in_bed"}


async def test_sync_registry_deactivates_entity_that_lost_the_label(cfg, db, fake_hass):
    both = [entity_entry("input_boolean.a"), entity_entry("input_boolean.b")]
    prepare(fake_hass, both)
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    collector = make_collector(cfg, db, fake_hass)
    try:
        await collector.sync_registry(client)
        set_registry(fake_hass, [entity_entry("input_boolean.a"), entity_entry("input_boolean.b", labels=())])
        await collector.sync_registry(client)
    finally:
        await client.close()
    row = db.conn.execute("SELECT active, tracked_until FROM entities WHERE entity_id='input_boolean.b'").fetchone()
    assert row["active"] == 0 and row["tracked_until"] is not None
    assert set(collector.pipeline.rules) == {"input_boolean.a"}


# --- Lückenabgleich ----------------------------------------------------------

async def test_gap_fill_replays_history_with_gap_source(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    seen: dict = {}

    def history(message):
        seen.update(message)
        return {"input_boolean.someone_in_bed": [
            {"s": "off", "a": {}, "lu": 1767261600.0},
            {"s": "on", "a": {}, "lu": 1767265200.0},
        ]}

    fake_hass.responses[HISTORY] = history
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    collector = make_collector(cfg, db, fake_hass)
    try:
        await collector.sync_registry(client)
        assert await collector.gap_fill(client) == 2
    finally:
        await client.close()
    rows = db.conn.execute("SELECT state, source FROM state_changes ORDER BY ts_utc").fetchall()
    assert [(r["state"], r["source"]) for r in rows] == [("off", "gap:history_api"), ("on", "gap:history_api")]
    assert seen["entity_ids"] == ["input_boolean.someone_in_bed"]
    assert seen["include_start_time_state"] is True


# --- Sitzung ---------------------------------------------------------------------

async def test_run_session_records_events_and_flushes_on_close(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    fake_hass.events = [
        state_event("input_boolean.someone_in_bed", "on", "2026-01-01T22:00:00+00:00", user="u-fabian"),
        state_event("light.nicht_gelabelt", "on", "2026-01-01T22:00:01+00:00"),
        {"event_type": "state_changed", "data": {"entity_id": "x", "new_state": None}},
    ]
    fake_hass.close_after_command = HISTORY
    client = HassClient(fake_hass.url, "secret")
    collector = make_collector(cfg, db, fake_hass)
    with pytest.raises(HassConnectionError):
        await collector.run_session(client)
    await client.close()
    rows = db.conn.execute("SELECT entity_id, state, context_user_id, source FROM state_changes").fetchall()
    assert [tuple(r) for r in rows] == [("input_boolean.someone_in_bed", "on", "u-fabian", "live")]


async def test_periodic_flush_writes_before_the_session_ends(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    fake_hass.events = [state_event("input_boolean.someone_in_bed", "on", "2026-01-01T22:00:00+00:00")]
    collector = make_collector(replace(cfg, flush_interval_s=0.05), db, fake_hass)
    client = HassClient(fake_hass.url, "secret")
    task = asyncio.create_task(collector.run_session(client))
    count = 0
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            count = db.conn.execute("SELECT COUNT(*) FROM state_changes").fetchone()[0]
            if count:
                break
        assert count == 1
    finally:
        collector.stop()
        await task
        await client.close()


async def test_health_file_is_touched_after_connect(cfg, db, fake_hass, tmp_path):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    fake_hass.close_after_command = HISTORY
    health = tmp_path / "state" / "collector.health"
    collector = make_collector(cfg, db, fake_hass, health_file=health)
    client = HassClient(fake_hass.url, "secret")
    with pytest.raises(HassConnectionError):
        await collector.run_session(client)
    await client.close()
    assert health.exists()


async def test_run_stops_on_stop_request(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    collector = make_collector(cfg, db, fake_hass)
    task = asyncio.create_task(collector.run())
    await asyncio.sleep(0.3)
    collector.stop()
    await asyncio.wait_for(task, 5)


async def test_run_reconnects_after_connection_loss(cfg, db, fake_hass, monkeypatch):
    monkeypatch.setattr(live, "BACKOFF_START", 0.05)
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    fake_hass.close_after_command = HISTORY
    collector = make_collector(cfg, db, fake_hass)
    task = asyncio.create_task(collector.run())
    try:
        subscriptions = 0
        for _ in range(100):
            await asyncio.sleep(0.05)
            subscriptions = len([m for m in fake_hass.received if m["type"] == "subscribe_events"])
            if subscriptions >= 2:
                break
        assert subscriptions >= 2
    finally:
        collector.stop()
        await asyncio.wait_for(task, 5)


async def test_run_does_not_retry_on_auth_error(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    fake_hass.token = "anderes-token"
    collector = make_collector(cfg, db, fake_hass)
    with pytest.raises(HassAuthError):
        await collector.run()
