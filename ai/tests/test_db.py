from datetime import datetime, timedelta, timezone

from collector.db import Database
from collector.models import Change, EntityRule

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def rule(entity_id="climate.bt", kind="climate", domain="heating") -> EntityRule:
    return EntityRule(entity_id=entity_id, kind=kind, domain=domain, attrs=("temperature",))


def change(seconds, state="heat", entity_id="climate.bt", **attrs) -> Change:
    return Change(T0 + timedelta(seconds=seconds), entity_id, state, attrs)


def test_upsert_entity_reports_new_unchanged_and_reactivated(db: Database):
    assert db.upsert_entity(rule(), "buero", T0) == "new"
    assert db.upsert_entity(rule(), "buero", T0) == "unchanged"
    db.deactivate_missing(set(), T0 + timedelta(hours=1))
    assert db.upsert_entity(rule(), "buero", T0 + timedelta(hours=2)) == "reactivated"
    row = db.conn.execute("SELECT active, tracked_until FROM entities WHERE entity_id='climate.bt'").fetchone()
    assert (row["active"], row["tracked_until"]) == (1, None)


def test_deactivate_missing_keeps_rows_and_sets_tracked_until(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    db.insert_changes([change(0, temperature=17)], "live")
    gone = db.deactivate_missing(set(), T0 + timedelta(hours=1))
    assert gone == ["climate.bt"]
    row = db.conn.execute("SELECT active, tracked_until FROM entities").fetchone()
    assert row["active"] == 0
    assert row["tracked_until"] == "2026-01-01T11:00:00.000000+00:00"
    assert db.count_changes("climate.bt") == 1


def test_insert_changes_is_idempotent_and_counts_new_rows(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    rows = [change(0, temperature=17), change(10, temperature=21)]
    assert db.insert_changes(rows, "live") == 2
    assert db.insert_changes(rows, "backfill:a.db") == 0
    assert db.count_changes("climate.bt") == 2


def test_insert_changes_adds_missing_context_to_existing_row(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    plain = Change(T0, "climate.bt", "heat", {"temperature": 21})
    with_context = Change(T0, "climate.bt", "heat", {"temperature": 21}, "c1", "u1", None)
    assert db.insert_changes([plain], "gap:history_api") == 1
    assert db.insert_changes([with_context], "backfill:a.db") == 0     # keine neue Zeile
    row = db.conn.execute("SELECT context_id, context_user_id, source FROM state_changes").fetchone()
    assert (row["context_id"], row["context_user_id"]) == ("c1", "u1")
    assert db.count_changes("climate.bt") == 1


def test_insert_changes_does_not_overwrite_existing_context(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    first = Change(T0, "climate.bt", "heat", {"temperature": 21}, "c1", "u1", None)
    second = Change(T0, "climate.bt", "heat", {"temperature": 21}, "c2", "u2", None)
    db.insert_changes([first], "live")
    db.insert_changes([second], "backfill:a.db")
    row = db.conn.execute("SELECT context_id, context_user_id FROM state_changes").fetchone()
    assert (row["context_id"], row["context_user_id"]) == ("c1", "u1")


def test_insert_changes_lowers_tracked_since(db: Database):
    db.upsert_entity(rule(), "buero", T0 + timedelta(days=10))
    db.insert_changes([change(0, temperature=17)], "backfill:a.db")
    row = db.conn.execute("SELECT tracked_since FROM entities").fetchone()
    assert row["tracked_since"] == "2026-01-01T10:00:00.000000+00:00"


def test_insert_changes_stores_unavailable_without_attrs(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    db.insert_changes([Change(T0, "climate.bt", "unavailable", {})], "live")
    row = db.conn.execute("SELECT state, attrs FROM state_changes").fetchone()
    assert (row["state"], row["attrs"]) == ("unavailable", "{}")


def test_insert_changes_tolerates_non_json_attribute_values(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    odd = Change(T0, "climate.bt", "heat", {"when": T0, "nested": {"a": [1, 2]}})
    assert db.insert_changes([odd], "live") == 1


def test_last_change_and_last_ts_return_newest(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    db.insert_changes([change(0, temperature=17), change(30, temperature=21), change(10, temperature=19)], "live")
    last = db.last_change("climate.bt")
    assert last.attrs == {"temperature": 21}
    assert last.ts == T0 + timedelta(seconds=30)
    assert db.last_ts("climate.bt") == T0 + timedelta(seconds=30)
    assert db.last_ts("climate.nope") is None
    assert db.last_change("climate.nope") is None


def test_insert_without_commit_can_be_rolled_back(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    db.insert_changes([change(0, temperature=17)], "backfill:a.db", commit=False)
    db.rollback()
    assert db.count_changes("climate.bt") == 0


def test_sync_room_closes_old_row_when_area_changes(db: Database):
    db.sync_room("climate.bt", "buero", "climate", T0)
    db.sync_room("climate.bt", "buero", "climate", T0 + timedelta(hours=1))   # keine Änderung
    db.sync_room("climate.bt", "flur", "climate", T0 + timedelta(hours=2))
    rows = db.conn.execute("SELECT room, valid_from, valid_to FROM room_map ORDER BY valid_from").fetchall()
    assert [(r["room"], r["valid_to"] is None) for r in rows] == [("buero", False), ("flur", True)]


def test_extend_room_to_beginning_creates_row_when_none_exists(db: Database):
    db.extend_room_to_beginning("climate.bt", "buero", "climate")
    row = db.conn.execute("SELECT room, role, valid_from, valid_to FROM room_map").fetchone()
    assert tuple(row) == ("buero", "climate", "", None)


def test_extend_room_to_beginning_backdates_the_first_live_row(db: Database):
    db.sync_room("climate.bt", "buero", "climate", T0 + timedelta(days=100))
    db.extend_room_to_beginning("climate.bt", "buero", "climate")
    db.extend_room_to_beginning("climate.bt", "buero", "climate")       # idempotent
    rows = db.conn.execute("SELECT room, valid_from, valid_to FROM room_map").fetchall()
    assert [tuple(r) for r in rows] == [("buero", "", None)]
    db.sync_room("climate.bt", "buero", "climate", T0 + timedelta(days=101))   # nichts Neues
    assert db.conn.execute("SELECT COUNT(*) FROM room_map").fetchone()[0] == 1


def test_extend_room_to_beginning_only_changes_the_earliest_of_several_rows(db: Database):
    db.sync_room("climate.bt", "buero", "climate", T0)
    db.sync_room("climate.bt", "flur", "climate", T0 + timedelta(days=30))
    db.extend_room_to_beginning("climate.bt", "flur", "climate")
    rows = db.conn.execute("SELECT room, valid_from FROM room_map ORDER BY valid_from").fetchall()
    assert [(r["room"], r["valid_from"]) for r in rows] == [
        ("buero", ""), ("flur", "2026-01-31T10:00:00.000000+00:00")]


def test_add_history_room_is_idempotent(db: Database):
    for _ in range(2):
        db.add_history_room("buero", "climate.trv_office", "climate", None, "2025-12-01T00:00:00+00:00")
    assert db.conn.execute("SELECT COUNT(*) FROM room_map").fetchone()[0] == 1


def test_purge_entity_removes_all_rows(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    db.sync_room("climate.bt", "buero", "climate", T0)
    db.insert_changes([change(0, temperature=17), change(5, temperature=18)], "live")
    assert db.purge_entity("climate.bt") == 2
    assert db.count_changes("climate.bt") == 0
    assert db.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM room_map").fetchone()[0] == 0


def test_mark_inactive_sets_tracked_until(db: Database):
    db.upsert_entity(rule("climate.trv_office"), "buero", T0)
    db.mark_inactive("climate.trv_office", T0 + timedelta(days=5))
    row = db.conn.execute("SELECT active, tracked_until FROM entities").fetchone()
    assert (row["active"], row["tracked_until"]) == (0, "2026-01-06T10:00:00.000000+00:00")


def test_backup_to_creates_readable_copy(db: Database, tmp_path):
    db.upsert_entity(rule(), "buero", T0)
    db.insert_changes([change(0, temperature=17)], "live")
    target = tmp_path / "sub" / "copy.sqlite"
    db.backup_to(target)
    copy = Database(target)
    assert copy.count_changes("climate.bt") == 1
    copy.close()


def test_record_run_and_users(db: Database):
    db.record_run("backfill:a.db", "a", "b", 5, "ok", "c")
    db.upsert_user("u1", "person.fabian", "Fabian")
    db.upsert_user("u1", "person.fabian", "Fabian W.")
    assert db.conn.execute("SELECT rows, status FROM ingest_runs").fetchone()["rows"] == 5
    assert db.conn.execute("SELECT name FROM users WHERE user_id='u1'").fetchone()["name"] == "Fabian W."


def test_active_entity_ids(db: Database):
    db.upsert_entity(rule("climate.a"), None, T0)
    db.upsert_entity(rule("climate.b"), None, T0)
    db.deactivate_missing({"climate.a"}, T0)
    assert db.active_entity_ids() == {"climate.a"}
