import json
from datetime import datetime, timedelta, timezone

from analysis.interventions import load_interventions
from collector.models import Change, EntityRule, to_utc_iso

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
CLIMATE = EntityRule(entity_id="climate.bt", kind="climate", domain="heating", attrs=("temperature", "preset_mode"))


def add(db, seconds, temperature, origin=None, actor=None, automation=None, user=None, parent=None):
    ts = T0 + timedelta(seconds=seconds)
    db.insert_changes([Change(ts, "climate.bt", "heat", {"temperature": temperature, "preset_mode": "eco"},
                              f"c{seconds}", user, parent)], "live")
    if origin is not None:
        sid = db.conn.execute("SELECT id FROM state_changes WHERE ts_utc = ?", (to_utc_iso(ts),)).fetchone()["id"]
        db.conn.execute("INSERT INTO change_origin(state_change_id, origin, via, actor, automation) "
                        "VALUES (?, ?, 'kontext', ?, ?)", (sid, origin, actor, automation))
        db.conn.commit()


def rows(db):
    return db.conn.execute("SELECT change_id, actor_class, actor_user_id, origin_automation, new "
                           "FROM manual_actions ORDER BY ts_utc").fetchall()


def test_origin_overrides_the_context_heuristic(db):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, 17)
    add(db, 600, 18, origin="geraet_unklar")
    add(db, 1200, 19, origin="automation", automation="automation.hc", user="u1")
    add(db, 1800, 20, origin="benutzer", actor="u7")
    add(db, 2400, 21, origin="unbekannt")
    result = rows(db)
    assert [r["actor_class"] for r in result] == ["device", "automation", "manual", "unknown"]
    assert [r["actor_user_id"] for r in result] == [None, "u1", "u7", None]
    assert result[1]["origin_automation"] == "automation.hc"
    assert all(r["change_id"] for r in result)


def test_rows_without_origin_keep_the_old_behaviour(db):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, 17)
    add(db, 60, 18, user="u1")
    assert [r["actor_class"] for r in rows(db)] == ["manual"]


def test_interventions_include_devices_but_not_automations(db, acfg):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, 17)
    add(db, 600, 18, origin="geraet_unklar")
    add(db, 1200, 21, origin="automation", automation="automation.hc")
    add(db, 1800, 19, origin="benutzer", actor="u1")
    found = load_interventions(db, acfg)
    assert [(item.kind, item.value, item.user_id) for item in found] == [
        ("setpoint", 18.0, None), ("setpoint", 19.0, "u1")]
