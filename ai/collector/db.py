from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import Change, EntityRule, attrs_hash, attrs_json, parse_utc, to_utc_iso

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


class Database:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # --- Entitäten ---------------------------------------------------------

    def upsert_entity(self, rule: EntityRule, room: str | None, now: datetime) -> str:
        row = self.conn.execute("SELECT active FROM entities WHERE entity_id = ?", (rule.entity_id,)).fetchone()
        status = "new" if row is None else ("reactivated" if row["active"] == 0 else "unchanged")
        self.conn.execute(
            """
            INSERT INTO entities(entity_id, domain, kind, room, tracked_attrs, min_delta, heartbeat_s,
                                 debounce_s, tracked_since, active, tracked_until)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)
            ON CONFLICT(entity_id) DO UPDATE SET
              domain = excluded.domain, kind = excluded.kind, room = excluded.room,
              tracked_attrs = excluded.tracked_attrs, min_delta = excluded.min_delta,
              heartbeat_s = excluded.heartbeat_s, debounce_s = excluded.debounce_s,
              active = 1, tracked_until = NULL
            """,
            (rule.entity_id, rule.domain, rule.kind, room, json.dumps(list(rule.attrs)),
             rule.numeric_state_delta, rule.heartbeat_s, rule.debounce_s, to_utc_iso(now)),
        )
        self.conn.commit()
        return status

    def mark_inactive(self, entity_id: str, until: datetime) -> None:
        self.conn.execute(
            "UPDATE entities SET active = 0, tracked_until = ? WHERE entity_id = ?",
            (to_utc_iso(until), entity_id),
        )
        self.conn.commit()

    def deactivate_missing(self, active_ids: set[str], now: datetime) -> list[str]:
        rows = self.conn.execute("SELECT entity_id FROM entities WHERE active = 1").fetchall()
        gone = [row["entity_id"] for row in rows if row["entity_id"] not in active_ids]
        for entity_id in gone:
            self.conn.execute(
                "UPDATE entities SET active = 0, tracked_until = ? WHERE entity_id = ?",
                (to_utc_iso(now), entity_id),
            )
        self.conn.commit()
        return gone

    def active_entity_ids(self) -> set[str]:
        rows = self.conn.execute("SELECT entity_id FROM entities WHERE active = 1").fetchall()
        return {row["entity_id"] for row in rows}

    # --- Räume und Benutzer -------------------------------------------------

    def sync_room(self, entity_id: str, room: str | None, role: str, now: datetime) -> None:
        if room is None:
            return
        current = self.conn.execute(
            "SELECT room, role FROM room_map WHERE entity_id = ? AND valid_to IS NULL", (entity_id,)
        ).fetchone()
        if current is not None and current["room"] == room and current["role"] == role:
            return
        stamp = to_utc_iso(now)
        if current is not None:
            self.conn.execute(
                "UPDATE room_map SET valid_to = ? WHERE entity_id = ? AND valid_to IS NULL", (stamp, entity_id)
            )
        self.conn.execute(
            "INSERT OR IGNORE INTO room_map(room, entity_id, role, valid_from, valid_to) VALUES (?, ?, ?, ?, NULL)",
            (room, entity_id, role, stamp),
        )
        self.conn.commit()

    def add_history_room(self, room: str, entity_id: str, role: str,
                         valid_from: str | None, valid_to: str | None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO room_map(room, entity_id, role, valid_from, valid_to) VALUES (?, ?, ?, ?, ?)",
            (room, entity_id, role, valid_from or "", valid_to),
        )
        self.conn.commit()

    def upsert_user(self, user_id: str, person_entity: str | None, name: str | None) -> None:
        self.conn.execute(
            """
            INSERT INTO users(user_id, person_entity, name) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET person_entity = excluded.person_entity, name = excluded.name
            """,
            (user_id, person_entity, name),
        )
        self.conn.commit()

    # --- Zustandsänderungen -------------------------------------------------

    def insert_changes(self, changes: Iterable[Change], source: str, commit: bool = True) -> int:
        rows = []
        first_seen: dict[str, str] = {}
        for change in changes:
            stamp = to_utc_iso(change.ts)
            rows.append((stamp, change.entity_id, change.state, attrs_json(change.attrs),
                         attrs_hash(change.attrs), change.context_id, change.context_user_id,
                         change.context_parent_id, source))
            if change.entity_id not in first_seen or stamp < first_seen[change.entity_id]:
                first_seen[change.entity_id] = stamp
        if not rows:
            return 0
        before = self.conn.total_changes
        try:
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO state_changes(ts_utc, entity_id, state, attrs, attrs_hash,
                  context_id, context_user_id, context_parent_id, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            inserted = self.conn.total_changes - before
            # Eine Zeile ohne Kontext (z. B. aus der History-API) bekommt den Kontext nachgetragen,
            # wenn dieselbe Änderung mit Kontext eintrifft; vorhandener Kontext bleibt unverändert.
            self.conn.executemany(
                """
                UPDATE state_changes SET context_id = ?, context_user_id = ?, context_parent_id = ?
                WHERE entity_id = ? AND ts_utc = ? AND state IS ? AND attrs_hash = ?
                  AND context_id IS NULL AND context_user_id IS NULL AND context_parent_id IS NULL
                """,
                [(row[5], row[6], row[7], row[1], row[0], row[2], row[4])
                 for row in rows if row[5] or row[6] or row[7]],
            )
            for entity_id, stamp in first_seen.items():
                self.conn.execute(
                    "UPDATE entities SET tracked_since = ? "
                    "WHERE entity_id = ? AND (tracked_since IS NULL OR tracked_since > ?)",
                    (stamp, entity_id, stamp),
                )
            if commit:
                self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return inserted

    def last_change(self, entity_id: str) -> Change | None:
        row = self.conn.execute(
            "SELECT * FROM state_changes WHERE entity_id = ? ORDER BY ts_utc DESC, id DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
        if row is None:
            return None
        return Change(parse_utc(row["ts_utc"]), row["entity_id"], row["state"],
                      json.loads(row["attrs"] or "{}"), row["context_id"],
                      row["context_user_id"], row["context_parent_id"])

    def last_ts(self, entity_id: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(ts_utc) AS ts FROM state_changes WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        return parse_utc(row["ts"]) if row["ts"] else None

    def count_changes(self, entity_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM state_changes WHERE entity_id = ?", (entity_id,)
        ).fetchone()[0]

    # --- Verwaltung ------------------------------------------------------------

    def record_run(self, source: str, from_ts: str | None, to_ts: str | None,
                   rows: int, status: str, started_at: str) -> None:
        self.conn.execute(
            "INSERT INTO ingest_runs(source, from_ts, to_ts, rows, status, started_at) VALUES (?, ?, ?, ?, ?, ?)",
            (source, from_ts, to_ts, rows, status, started_at),
        )
        self.conn.commit()

    def purge_entity(self, entity_id: str) -> int:
        deleted = self.count_changes(entity_id)
        self.conn.execute("DELETE FROM state_changes WHERE entity_id = ?", (entity_id,))
        self.conn.execute("DELETE FROM room_map WHERE entity_id = ?", (entity_id,))
        self.conn.execute("DELETE FROM entities WHERE entity_id = ?", (entity_id,))
        self.conn.commit()
        return deleted

    def backup_to(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        destination = sqlite3.connect(str(temporary))
        try:
            self.conn.backup(destination)
        finally:
            destination.close()
        temporary.replace(target)
