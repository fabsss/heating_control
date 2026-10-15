import asyncio
import json
from datetime import timedelta

import pytest

from collector.models import Change, EntityRule, to_utc_iso
from execution.models import Deployment
from execution.mqtt import InMemoryBus
from execution.service import Service
from execution.store import ExecStore
from tests.ha_template import utc

T0 = utc(2026, 10, 15, 4, 30)
NOW = T0 + timedelta(hours=3)


def fired(ts=T0, **overrides):
    data = {"ts": ts.isoformat() if hasattr(ts, "isoformat") else ts, "rule_id": "r5", "room": "climate.bt",
            "mode": "schatten", "result": "nicht_ausgefuehrt", "reason": "schatten",
            "target": {"kind": "preset", "value": "comfort"},
            "current": {"preset": "eco", "temperature": 17.0, "hvac_mode": "heat"}}
    data.update(overrides)
    return json.dumps(data)


@pytest.fixture
def setup(db, ecfg):
    store = ExecStore(db)
    store.add_deployment(Deployment("r5", 5, "bad", "automation.ki_regel_r5", "homeai_rule_r5", "climate.bt",
                                    "schatten", "1.0", "Bad: x", "2026-10-15T00:00:00.000000+00:00"))
    bus = InMemoryBus()
    service = Service(ecfg, db, store, bus, now=lambda: NOW)
    service.register()
    return service, store, bus


async def test_fired_message_is_stored_once_and_publishes_stats(setup):
    service, store, bus = setup
    await bus.deliver("homeai/rule/r5/fired", fired())
    await bus.deliver("homeai/rule/r5/fired", fired())
    (event,) = store.pending_events()
    assert (event.rule_id, event.mode, event.result, event.reason) == ("r5", "schatten", "nicht_ausgefuehrt", "schatten")
    assert event.ts_utc == to_utc_iso(T0) and event.target == {"kind": "preset", "value": "comfort"}
    stats = json.loads(bus.retained["homeai/rule/r5/stats"])
    assert stats["events"] == 1 and stats["reife"] == "zu_wenig_daten"


async def test_offset_timestamps_are_normalised_to_utc(setup):
    service, store, bus = setup
    await bus.deliver("homeai/rule/r5/fired", fired(ts="2026-10-15T06:30:00+02:00"))
    assert store.pending_events()[0].ts_utc == to_utc_iso(T0)


@pytest.mark.parametrize("topic,payload", [
    ("homeai/rule/zz/fired", None),                       # unbekannte Regel
    ("homeai/rule/r5/fired", "kein json"),
    ("homeai/rule/r5/fired", json.dumps({"mode": "aktiv"})),      # ohne Zeitstempel
])
async def test_unusable_messages_are_ignored(setup, topic, payload):
    service, store, bus = setup
    await bus.deliver(topic, payload or fired())
    assert store.pending_events() == []


async def test_removed_rules_are_ignored(setup):
    service, store, bus = setup
    store.set_deployment_state("r5", "entfernt", NOW)
    await bus.deliver("homeai/rule/r5/fired", fired())
    assert store.pending_events() == []


async def test_switch_commands_are_recorded(setup):
    service, store, bus = setup
    await bus.deliver("homeai/rule/r5/active/set", "ON")
    assert store.get_deployment("r5").state == "aktiv"
    await bus.deliver("homeai/rule/r5/active/set", "OFF")
    assert store.get_deployment("r5").state == "schatten"
    await bus.deliver("homeai/rule/r5/active/set", "kaputt")
    assert store.get_deployment("r5").state == "schatten"
    await bus.deliver("homeai/rule/zz/active/set", "ON")


def add_change(db, minutes, temperature, preset, origin):
    ts = T0 + timedelta(minutes=minutes)
    db.insert_changes([Change(ts, "climate.bt", "heat", {"temperature": temperature, "preset_mode": preset})], "live")
    sid = db.conn.execute("SELECT id FROM state_changes WHERE ts_utc = ?", (to_utc_iso(ts),)).fetchone()["id"]
    db.conn.execute("INSERT INTO change_origin(state_change_id, origin, via) VALUES (?, ?, 'kontext')", (sid, origin))
    db.conn.commit()


async def test_pending_events_are_evaluated_after_their_window(setup, db):
    service, store, bus = setup
    db.upsert_entity(EntityRule("climate.bt", "climate", "heating", attrs=("temperature", "preset_mode")), "bad", T0)
    add_change(db, -300, 17, "eco", "benutzer")
    add_change(db, 10, 21, "comfort", "benutzer")
    await bus.deliver("homeai/rule/r5/fired", fired())
    await bus.deliver("homeai/rule/r5/fired", fired(ts=(NOW - timedelta(minutes=30)).isoformat()))
    assert await service.evaluate_pending() == 1                       # der zweite Eintrag wartet noch
    assert len(store.pending_events()) == 1
    (first, _) = store.stats_rows("r5")
    assert (first["outcome"], first["lage"], first["abstand_min"]) == ("treffer", "nachher", 10.0)
    stats = json.loads(bus.retained["homeai/rule/r5/stats"])
    assert stats["treffer"] == 1 and stats["scored"] == 1


async def test_unscored_reasons_get_a_marker_outcome(setup):
    service, store, bus = setup
    await bus.deliver("homeai/rule/r5/fired", fired(reason="wohnungszustand"))
    assert await service.evaluate_pending() == 1
    assert store.pending_events() == []
    assert store.stats_rows("r5")[0]["outcome"] == "nicht_gewertet"


async def test_publish_all_stats_covers_every_active_rule(setup):
    service, store, bus = setup
    await service.publish_all_stats()
    assert "homeai/rule/r5/stats" in bus.retained


async def test_run_loop_evaluates_and_stops(setup):
    service, store, bus = setup
    await bus.deliver("homeai/rule/r5/fired", fired(reason="wohnungszustand"))
    stop = asyncio.Event()
    task = asyncio.create_task(service.run_loop(stop, interval=0.05))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if store.pending_events() == []:
            break
    stop.set()
    await asyncio.wait_for(task, 2)
    assert store.pending_events() == []
