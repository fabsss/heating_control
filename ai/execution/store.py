from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from collector.db import Database
from collector.models import to_utc_iso

from .models import Deployment, RuleEvent

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "execution.sql"


def _deployment(row) -> Deployment:
    return Deployment(row["rule_id"], row["candidate_id"], row["room"], row["ha_automation"], row["config_id"],
                      row["climate_entity"], row["state"], row["blueprint_version"], row["description"],
                      row["created_at"], row["removed_at"])


def _event(row) -> RuleEvent:
    return RuleEvent(row["rule_id"], row["ts_utc"], row["mode"], row["result"], row["reason"],
                     json.loads(row["target"]), json.loads(row["current_state"]), row["payload"], row["id"])


class ExecStore:
    def __init__(self, db: Database):
        self.conn = db.conn
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    # --- Regeln -------------------------------------------------------------------

    def add_deployment(self, d: Deployment) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO deployments(rule_id, candidate_id, room, ha_automation, config_id, climate_entity, "
            "state, blueprint_version, description, created_at, removed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d.rule_id, d.candidate_id, d.room, d.ha_automation, d.config_id, d.climate_entity, d.state,
             d.blueprint_version, d.description, d.created_at, d.removed_at))
        self.conn.commit()

    def get_deployment(self, rule_id: str) -> Deployment | None:
        row = self.conn.execute("SELECT * FROM deployments WHERE rule_id = ?", (rule_id,)).fetchone()
        return None if row is None else _deployment(row)

    def list_deployments(self, *, include_removed: bool = False) -> list[Deployment]:
        sql = "SELECT * FROM deployments" + ("" if include_removed else " WHERE state <> 'entfernt'") + " ORDER BY rule_id"
        return [_deployment(row) for row in self.conn.execute(sql)]

    def set_deployment_state(self, rule_id: str, state: str, now: datetime) -> None:
        self.conn.execute(
            "UPDATE deployments SET state = ?, removed_at = ? WHERE rule_id = ?",
            (state, to_utc_iso(now) if state == "entfernt" else None, rule_id))
        self.conn.commit()

    # --- Ereignisse und Ergebnisse -------------------------------------------------

    def add_event(self, event: RuleEvent) -> bool:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO rule_events(rule_id, ts_utc, mode, result, reason, target, current_state, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event.rule_id, event.ts_utc, event.mode, event.result, event.reason,
             json.dumps(event.target, ensure_ascii=False), json.dumps(event.current_state, ensure_ascii=False),
             event.payload))
        self.conn.commit()
        return cursor.rowcount > 0

    def pending_events(self) -> list[RuleEvent]:
        rows = self.conn.execute(
            "SELECT e.* FROM rule_events e LEFT JOIN rule_outcomes o ON o.rule_event_id = e.id "
            "WHERE o.rule_event_id IS NULL ORDER BY e.ts_utc")
        return [_event(row) for row in rows]

    def save_outcome(self, event_id: int, outcome: str, window_start: datetime, window_end: datetime, *,
                     lage: str | None = None, abstand_min: float | None = None, deviation: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO rule_outcomes(rule_event_id, outcome, lage, abstand_min, window_start, window_end, deviation) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, outcome, lage, abstand_min, to_utc_iso(window_start), to_utc_iso(window_end), deviation))
        self.conn.commit()

    def recent_events(self, rule_id: str, limit: int = 10) -> list[RuleEvent]:
        rows = self.conn.execute(
            "SELECT * FROM rule_events WHERE rule_id = ? ORDER BY ts_utc DESC LIMIT ?", (rule_id, limit))
        return [_event(row) for row in rows]

    def stats_rows(self, rule_id: str) -> list:
        return self.conn.execute(
            "SELECT e.id, e.mode, e.result, e.reason, e.target, o.outcome, o.lage, o.abstand_min, o.deviation "
            "FROM rule_events e LEFT JOIN rule_outcomes o ON o.rule_event_id = e.id WHERE e.rule_id = ? "
            "ORDER BY e.ts_utc", (rule_id,)).fetchall()

    # --- Aufträge ------------------------------------------------------------------

    def start_job(self, name: str, params: dict, now: datetime) -> int:
        cursor = self.conn.execute(
            "INSERT INTO jobs(name, params, state, started_at) VALUES (?, ?, 'laeuft', ?)",
            (name, json.dumps(params, ensure_ascii=False), to_utc_iso(now)))
        self.conn.commit()
        return cursor.lastrowid

    def finish_job(self, job_id: int, state: str, message: str, now: datetime) -> None:
        self.conn.execute("UPDATE jobs SET state = ?, finished_at = ?, message = ? WHERE id = ?",
                          (state, to_utc_iso(now), message, job_id))
        self.conn.commit()

    def recent_jobs(self, limit: int = 10) -> list:
        return self.conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def count_jobs_since(self, name: str, since: datetime) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE name = ? AND started_at >= ?", (name, to_utc_iso(since))).fetchone()[0]
