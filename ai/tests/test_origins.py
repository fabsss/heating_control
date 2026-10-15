from datetime import datetime, timedelta, timezone

from collector.db import Database
from collector.models import AutomationRun, Change, EntityRule, ServiceCall, to_utc_iso
from collector.origins import update_origins

T0 = datetime(2026, 10, 15, 11, 0, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(hours=1)
CLIMATE = EntityRule(entity_id="climate.bt", kind="climate", domain="heating", attrs=("temperature", "preset_mode"))


def prepare(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    db.mark_capture_start("calls_since", T0)


def change(db, seconds, temperature, *, ctx=None, user=None, parent=None, source="live"):
    db.insert_changes([Change(T0 + timedelta(seconds=seconds), "climate.bt", "heat",
                              {"temperature": temperature, "preset_mode": "eco"}, ctx, user, parent)], source)


def call(db, seconds, *, ctx=None, user=None, parent=None, entities=("climate.bt",)):
    db.insert_service_call(ServiceCall(T0 + timedelta(seconds=seconds), "climate", "set_temperature",
                                       tuple(entities), {}, ctx, user, parent))


def origin_of(db, seconds):
    row = db.conn.execute(
        "SELECT co.origin, co.via, co.actor, co.automation FROM change_origin co "
        "JOIN state_changes sc ON sc.id = co.state_change_id WHERE sc.ts_utc = ?",
        (to_utc_iso(T0 + timedelta(seconds=seconds)),)).fetchone()
    return None if row is None else tuple(row)


def test_user_click_is_matched_by_context(db):
    prepare(db)
    call(db, 10.0, ctx="c1", user="u1")
    change(db, 10.0004, 21, ctx="c1", user="u1")
    update_origins(db, NOW)
    assert origin_of(db, 10.0004) == ("benutzer", "kontext", "u1", None)


def test_user_click_is_matched_by_time_when_the_state_lost_the_context(db):
    prepare(db)
    call(db, 10.0, ctx="c1", user="u1")
    change(db, 10.3, 21, ctx="other")
    update_origins(db, NOW)
    assert origin_of(db, 10.3) == ("benutzer", "zeit", "u1", None)


def test_automation_call_names_the_automation(db):
    prepare(db)
    db.insert_automation_run(AutomationRun(T0 + timedelta(seconds=19), "automation.hc", "Heating Control", "a1"))
    call(db, 20.0, ctx="a1")
    change(db, 20.01, 18, ctx="a1")
    update_origins(db, NOW)
    assert origin_of(db, 20.01) == ("automation", "kontext", None, "automation.hc")


def test_call_with_parent_is_an_automation_even_with_a_user(db):
    prepare(db)
    call(db, 20.0, ctx="s1", user="u1", parent="p1")
    change(db, 20.01, 18, ctx="s1")
    update_origins(db, NOW)
    assert origin_of(db, 20.01)[0] == "automation"


def test_state_with_user_beats_an_unrelated_automation_call(db):
    prepare(db)
    call(db, 20.0, ctx="a1")
    change(db, 20.4, 18, ctx="x", user="u9")
    update_origins(db, NOW)
    assert origin_of(db, 20.4) == ("benutzer", "zeit", "u9", None)


def test_turning_the_device_has_no_call(db):
    prepare(db)
    change(db, 30, 22)
    update_origins(db, NOW)
    assert origin_of(db, 30) == ("geraet_unklar", "keine", None, None)


def test_calls_for_other_entities_do_not_match(db):
    prepare(db)
    call(db, 30.0, ctx="c2", user="u1", entities=("climate.other",))
    change(db, 30.1, 22)
    update_origins(db, NOW)
    assert origin_of(db, 30.1)[0] == "geraet_unklar"


def test_area_targeted_call_matches_by_time(db):
    prepare(db)
    call(db, 40.0, ctx="c3", user="u1", entities=())
    change(db, 40.2, 23, ctx="y")
    update_origins(db, NOW)
    assert origin_of(db, 40.2) == ("benutzer", "zeit", "u1", None)


def test_rows_from_gaps_without_context_are_unknown(db):
    prepare(db)
    change(db, 50, 20, source="gap:history_api")
    update_origins(db, NOW)
    assert origin_of(db, 50) == ("unbekannt", "keine", None, None)


def test_rows_before_the_capture_started_are_unknown(db):
    db.upsert_entity(CLIMATE, "buero", T0)
    db.mark_capture_start("calls_since", T0 + timedelta(minutes=10))
    change(db, 50, 20)
    update_origins(db, NOW)
    assert origin_of(db, 50)[0] == "unbekannt"


def test_state_context_alone_still_classifies_old_rows(db):
    prepare(db)
    change(db, 60, 20, user="u2", source="backfill:x.db")
    change(db, 70, 21, parent="p9", source="backfill:x.db")
    update_origins(db, NOW)
    assert origin_of(db, 60) == ("benutzer", "kontext", "u2", None)
    assert origin_of(db, 70) == ("automation", "kontext", None, None)


def test_young_rows_wait_for_their_call(db):
    prepare(db)
    change(db, 3598, 20)                       # zwei Sekunden vor NOW
    assert update_origins(db, NOW) == 0
    assert origin_of(db, 3598) is None


def test_second_run_is_idempotent_and_rebuild_reclassifies(db):
    prepare(db)
    change(db, 30, 22)
    assert update_origins(db, NOW) == 1
    assert update_origins(db, NOW) == 0
    call(db, 30.0, ctx="late", user="u2")
    update_origins(db, NOW)
    assert origin_of(db, 30)[0] == "geraet_unklar"
    assert update_origins(db, NOW, rebuild=True) == 1
    assert origin_of(db, 30) == ("benutzer", "zeit", "u2", None)


def test_only_climate_entities_get_an_origin(db):
    prepare(db)
    other = EntityRule(entity_id="input_boolean.x", kind="flat", domain="shared")
    db.upsert_entity(other, None, T0)
    db.insert_changes([Change(T0 + timedelta(seconds=5), "input_boolean.x", "on")], "live")
    update_origins(db, NOW)
    assert db.conn.execute("SELECT COUNT(*) FROM change_origin").fetchone()[0] == 0
