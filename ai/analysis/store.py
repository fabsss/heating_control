from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from collector.db import Database
from collector.models import to_utc_iso

from .models import Candidate

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "analysis.sql"


def _candidate(row) -> Candidate:
    return Candidate(
        room=row["room"],
        trigger=json.loads(row["trigger_json"]),
        conditions=json.loads(row["conditions_json"]),
        action=json.loads(row["action_json"]),
        hypothesis=row["hypothesis"] or "",
        status=row["status"],
        note=row["note"] or "",
        id=row["id"],
        run_id=row["run_id"],
    )


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store:
    """Tabellen der Analyse in derselben Datenbank wie die Datenbasis.

    `start_run` und `finish_run` committen. `add_candidate`, `set_candidate_status`,
    `save_results` und `save_pseudonyms` committen nicht: Der Aufrufer bündelt sie
    in einer Transaktion und ruft `commit()` oder `rollback()`.
    """

    def __init__(self, db: Database):
        self.conn = db.conn
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # --- Läufe -------------------------------------------------------------

    def start_run(self, mode: str, model: str, params: dict, now: datetime) -> int:
        cursor = self.conn.execute(
            "INSERT INTO analysis_runs(mode, model, started_at, params, status) VALUES (?, ?, ?, ?, 'laeuft')",
            (mode, model, to_utc_iso(now), _dump(params)),
        )
        self.conn.commit()
        return cursor.lastrowid

    def finish_run(self, run_id: int, status: str, now: datetime, *, error: str | None = None,
                   prompt_hash: str | None = None, prompt_text: str | None = None,
                   response_text: str | None = None, tokens_in: int | None = None,
                   tokens_out: int | None = None, cost_usd: float | None = None) -> None:
        self.conn.execute(
            """
            UPDATE analysis_runs SET status = ?, finished_at = ?, error = ?, prompt_hash = ?,
              prompt_text = ?, response_text = ?, tokens_in = ?, tokens_out = ?, cost_usd = ?
            WHERE id = ?
            """,
            (status, to_utc_iso(now), error, prompt_hash, prompt_text, response_text,
             tokens_in, tokens_out, cost_usd, run_id),
        )
        self.conn.commit()

    def get_run(self, run_id: int):
        return self.conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()

    def list_runs(self):
        return self.conn.execute("SELECT * FROM analysis_runs ORDER BY id").fetchall()

    # --- Kandidaten --------------------------------------------------------

    def add_candidate(self, run_id: int, candidate: Candidate) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO candidates(run_id, room, trigger_json, conditions_json, action_json,
                                   hypothesis, status, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, candidate.room, _dump(candidate.trigger), _dump(candidate.conditions),
             _dump(candidate.action), candidate.hypothesis, candidate.status, candidate.note),
        )
        return cursor.lastrowid

    def set_candidate_status(self, candidate_id: int, status: str, note: str | None = None) -> None:
        self.conn.execute("UPDATE candidates SET status = ?, note = COALESCE(?, note) WHERE id = ?",
                          (status, note, candidate_id))

    def get_candidate(self, candidate_id: int) -> Candidate:
        row = self.conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        if row is None:
            raise KeyError(f"Kandidat {candidate_id} existiert nicht")
        return _candidate(row)

    def list_candidates(self, run_id: int | None = None, status: str | None = None) -> list[Candidate]:
        sql, params = "SELECT * FROM candidates WHERE 1 = 1", []
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        return [_candidate(row) for row in self.conn.execute(sql + " ORDER BY id", params)]

    # --- Ergebnisse und Pseudonyme -------------------------------------------

    def save_results(self, candidate_id: int, metrics: dict[str, float]) -> None:
        self.conn.execute("DELETE FROM backtest_results WHERE candidate_id = ?", (candidate_id,))
        self.conn.executemany(
            "INSERT INTO backtest_results(candidate_id, metric, value) VALUES (?, ?, ?)",
            [(candidate_id, name, float(value)) for name, value in metrics.items()],
        )

    def results(self, candidate_id: int) -> dict[str, float]:
        rows = self.conn.execute(
            "SELECT metric, value FROM backtest_results WHERE candidate_id = ?", (candidate_id,))
        return {row["metric"]: row["value"] for row in rows}

    def save_pseudonyms(self, mapping: dict[str, tuple[str, str]]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO pseudonyms(alias, entity_id, kind) VALUES (?, ?, ?)",
            [(alias, entity_id, kind) for alias, (entity_id, kind) in mapping.items()],
        )

    def load_pseudonyms(self) -> dict[str, str]:
        return {row["alias"]: row["entity_id"] for row in self.conn.execute("SELECT * FROM pseudonyms")}
