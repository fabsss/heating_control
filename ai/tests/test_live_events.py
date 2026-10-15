from datetime import datetime, timedelta, timezone

import pytest

from collector.hass_ws import HassClient, HassConnectionError
from collector.live import automation_run_from_event, service_call_from_event
from tests.test_live import HISTORY, entity_entry, make_collector, prepare, state_event


def call_event(service, entity_id, stamp, *, user=None, parent=None, ctx="01CTX", domain="climate", extra=None):
    data = {} if entity_id is None else {"entity_id": entity_id}
    data.update(extra or {})
    return {"event_type": "call_service", "time_fired": stamp,
            "data": {"domain": domain, "service": service, "service_data": data},
            "context": {"id": ctx, "parent_id": parent, "user_id": user}}


def run_event(entity_id, stamp, name="Heating Control", ctx="01AUTO"):
    return {"event_type": "automation_triggered", "time_fired": stamp,
            "data": {"name": name, "entity_id": entity_id, "source": "state of x"},
            "context": {"id": ctx, "parent_id": None, "user_id": None}}


def test_service_call_with_entity_list_and_user():
    call = service_call_from_event(call_event(
        "set_preset_mode", ["climate.bt"], "2026-10-15T11:45:25.022794+00:00", user="u1",
        extra={"preset_mode": "comfort"}))
    assert (call.domain, call.service, call.entity_ids) == ("climate", "set_preset_mode", ("climate.bt",))
    assert call.data == {"preset_mode": "comfort"}
    assert (call.context_id, call.user_id, call.parent_id) == ("01CTX", "u1", None)
    assert call.ts.isoformat() == "2026-10-15T11:45:25.022794+00:00"


def test_service_call_with_single_entity_string():
    call = service_call_from_event(call_event("set_preset_mode", "climate.bt", "2026-10-15T11:46:17+00:00"))
    assert call.entity_ids == ("climate.bt",)


def test_service_call_without_entity_id_keeps_empty_targets():
    call = service_call_from_event(call_event("set_temperature", None, "2026-10-15T11:46:17+00:00",
                                              extra={"area_id": "bad"}))
    assert call.entity_ids == ()
    assert call.data == {"area_id": "bad"}


def test_service_calls_of_other_domains_are_ignored():
    assert service_call_from_event(call_event("turn_on", "fan.x", "2026-10-15T11:46:17+00:00", domain="fan")) is None
    assert service_call_from_event({}) is None


def test_automation_run_from_event():
    run = automation_run_from_event(run_event("automation.hc", "2026-10-15T01:00:00+00:00"))
    assert (run.automation_entity, run.name, run.context_id) == ("automation.hc", "Heating Control", "01AUTO")
    assert automation_run_from_event({"data": {}}) is None


async def test_run_session_stores_calls_and_runs_and_derives_origin(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("climate.better_thermostat_office")])
    base = datetime.now(timezone.utc) - timedelta(minutes=5)      # jünger als der Rückblick, älter als die Wartezeit

    def at(microseconds):
        return (base + timedelta(microseconds=microseconds)).isoformat()

    fake_hass.events = [state_event("climate.better_thermostat_office", "heat", at(500),
                                    {"temperature": 21, "preset_mode": "comfort"}, user="u-fabian", ctx="01CTX")]
    fake_hass.events_by_type = {
        "call_service": [
            call_event("set_preset_mode", ["climate.better_thermostat_office"], at(100), user="u-fabian"),
            call_event("set_temperature", ["climate.trv_office"], at(200), ctx="01TRV"),
            call_event("turn_on", ["fan.x"], at(300), domain="fan"),
        ],
        "automation_triggered": [run_event("automation.hc", at(0))],
    }
    fake_hass.close_after_command = HISTORY
    client = HassClient(fake_hass.url, "secret")
    collector = make_collector(cfg, db, fake_hass)
    with pytest.raises(HassConnectionError):
        await collector.run_session(client)
    await client.close()
    calls = db.conn.execute("SELECT service, entity_ids, user_id FROM service_calls").fetchall()
    assert [tuple(r) for r in calls] == [("set_preset_mode", '["climate.better_thermostat_office"]', "u-fabian")]
    runs = db.conn.execute("SELECT automation_entity FROM automation_runs").fetchall()
    assert [r["automation_entity"] for r in runs] == ["automation.hc"]
    assert db.capture_start("calls_since") is not None
    origin = db.conn.execute("SELECT origin, via, actor FROM change_origin").fetchall()
    assert [tuple(r) for r in origin] == [("benutzer", "kontext", "u-fabian")]
