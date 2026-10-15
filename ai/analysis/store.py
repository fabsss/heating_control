from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from collector.db import Database
from collector.models import to_utc_iso

from .models import Association, Candidate

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

    # --- Signale und Assoziationen ----------------------------------------------

    def assign_handles(self, entity_ids: list[str]) -> dict[str, str]:
        """Kennungen sig_NN: bestehende bleiben, neue Entitäten bekommen die nächste freie Nummer."""
        known = {row["entity_id"]: row["handle"]
                 for row in self.conn.execute("SELECT handle, entity_id FROM signals")}
        highest = max((int(handle.split("_")[1]) for handle in known.values()), default=0)
        for entity_id in sorted(entity_ids):
            if entity_id not in known:
                highest += 1
                known[entity_id] = f"sig_{highest:02d}"
                self.conn.execute(
                    "INSERT INTO signals(handle, entity_id, kind, value_kind, description) "
                    "VALUES (?, ?, '', 'none', '')", (known[entity_id], entity_id))
        self.conn.commit()
        return {entity_id: known[entity_id] for entity_id in entity_ids}

    def update_signal(self, handle: str, kind: str, value_kind: str, description: str) -> None:
        self.conn.execute("UPDATE signals SET kind = ?, value_kind = ?, description = ? WHERE handle = ?",
                          (kind, value_kind, description, handle))
        self.conn.commit()

    def list_signals(self):
        return self.conn.execute("SELECT * FROM signals ORDER BY handle").fetchall()

    def signal_descriptions(self) -> dict[str, str]:
        return {row["handle"]: row["description"] for row in self.list_signals()}

    def save_associations(self, run_id: int, items: list[Association]) -> None:
        self.conn.executemany(
            "INSERT INTO associations(run_id, signal, event, value, prev_class, room, win_start, win_end, "
            "events, hits, control_events, control_hits, lift, p, q, typical_action) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, a.signal, a.event, a.value, a.prev_class, a.room, a.win_start, a.win_end, a.events,
              a.hits, a.control_events, a.control_hits, a.lift, a.p, a.q, a.typical_action) for a in items])

    def list_associations(self, run_id: int) -> list[Association]:
        rows = self.conn.execute("SELECT * FROM associations WHERE run_id = ? ORDER BY q, -lift, id", (run_id,))
        return [Association(row["signal"], row["event"], row["value"], row["prev_class"], row["room"],
                            row["win_start"], row["win_end"], row["events"], row["hits"],
                            row["control_events"], row["control_hits"], row["lift"], row["p"], row["q"],
                            row["typical_action"] or "") for row in rows]
