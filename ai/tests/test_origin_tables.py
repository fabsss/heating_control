from datetime import datetime, timezone

from collector.db import Database
from collector.models import AutomationRun, ServiceCall

T0 = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)


def test_service_call_is_stored_once(db: Database):
    call = ServiceCall(T0, "climate", "set_preset_mode", ("climate.bt",), {"preset_mode": "comfort"},
                       "c1", "u1", None)
    assert db.insert_service_call(call) is True
    assert db.insert_service_call(call) is False
    row = db.conn.execute("SELECT * FROM service_calls").fetchone()
    assert (row["domain"], row["service"], row["context_id"], row["user_id"], row["parent_id"]) == (
        "climate", "set_preset_mode", "c1", "u1", None)
    assert row["entity_ids"] == '["climate.bt"]'
    assert row["data"] == '{"preset_mode":"comfort"}'


def test_automation_run_is_stored_once(db: Database):
    run = AutomationRun(T0, "automation.hc", "Heating Control", "c9")
    assert db.insert_automation_run(run) is True
    assert db.insert_automation_run(run) is False
    row = db.conn.execute("SELECT * FROM automation_runs").fetchone()
    assert (row["automation_entity"], row["name"], row["context_id"]) == ("automation.hc", "Heating Control", "c9")


def test_capture_start_keeps_the_first_value(db: Database):
    assert db.capture_start("calls_since") is None
    db.mark_capture_start("calls_since", T0)
    db.mark_capture_start("calls_since", datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert db.capture_start("calls_since") == T0


def test_change_origin_table_exists(db: Database):
    db.conn.execute("INSERT INTO change_origin(state_change_id, origin, via) VALUES (1, 'benutzer', 'kontext')")
    assert db.conn.execute("SELECT origin FROM change_origin").fetchone()["origin"] == "benutzer"
