from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from .db import Database
from .models import parse_utc, to_utc_iso

MATCH_WINDOW_S = 1.0      # Dienstaufruf und Zustandszeile liegen höchstens so weit auseinander
SETTLE_S = 5.0            # junge Zeilen warten, bis ihr Aufruf sicher gespeichert ist


@dataclass(frozen=True)
class Origin:
    origin: str           # benutzer | automation | geraet_unklar | unbekannt
    via: str              # kontext | zeit | keine
    actor: str | None = None
    automation: str | None = None


def _calls_around(db: Database, ts: datetime, entity_id: str) -> list:
    low = to_utc_iso(ts - timedelta(seconds=MATCH_WINDOW_S))
    high = to_utc_iso(ts + timedelta(seconds=MATCH_WINDOW_S))
    rows = db.conn.execute(
        "SELECT * FROM service_calls WHERE ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc", (low, high)).fetchall()
    result = []
    for row in rows:
        targets = json.loads(row["entity_ids"] or "[]")
        if targets and entity_id not in targets:
            continue
        result.append(row)
    return result


def _automation_for(db: Database, call) -> str | None:
    for context in (call["context_id"], call["parent_id"]):
        if not context:
            continue
        row = db.conn.execute(
            "SELECT automation_entity FROM automation_runs WHERE context_id = ? ORDER BY ts_utc LIMIT 1",
            (context,)).fetchone()
        if row is not None:
            return row["automation_entity"]
    return None


def _from_call(db: Database, call, via: str) -> Origin:
    if call["parent_id"]:
        return Origin("automation", via, None, _automation_for(db, call))
    if call["user_id"]:
        return Origin("benutzer", via, call["user_id"])
    return Origin("automation", via, None, _automation_for(db, call))


def classify_row(db: Database, row, calls_since: datetime | None) -> Origin:
    ts = parse_utc(row["ts_utc"])
    calls = _calls_around(db, ts, row["entity_id"])
    by_context = [call for call in calls if row["context_id"] and call["context_id"] == row["context_id"]]
    if by_context:
        found: Origin | None = _from_call(db, by_context[0], "kontext")
    elif calls:
        nearest = min(calls, key=lambda call: abs((parse_utc(call["ts_utc"]) - ts).total_seconds()))
        found = _from_call(db, nearest, "zeit")
    else:
        found = None
    if found is not None:
        if found.origin == "automation" and row["context_user_id"] and not row["context_parent_id"]:
            return Origin("benutzer", found.via, row["context_user_id"])
        return found
    if row["context_parent_id"]:
        return Origin("automation", "kontext")
    if row["context_user_id"]:
        return Origin("benutzer", "kontext", row["context_user_id"])
    if row["source"] == "live" and calls_since is not None and ts >= calls_since:
        return Origin("geraet_unklar", "keine")
    return Origin("unbekannt", "keine")


def update_origins(db: Database, now: datetime, *, rebuild: bool = False,
                   lookback: timedelta = timedelta(days=2)) -> int:
    """Berechnet die Herkunft für Thermostat-Zeilen ohne Eintrag; `rebuild` rechnet alle neu."""
    calls_since = db.capture_start("calls_since")
    if rebuild:
        db.conn.execute("DELETE FROM change_origin")
    sql = ("SELECT sc.* FROM state_changes sc JOIN entities e ON e.entity_id = sc.entity_id "
           "LEFT JOIN change_origin co ON co.state_change_id = sc.id "
           "WHERE e.kind = 'climate' AND co.state_change_id IS NULL AND sc.ts_utc <= ?")
    params: list = [to_utc_iso(now - timedelta(seconds=SETTLE_S))]
    if not rebuild:
        sql += " AND sc.ts_utc >= ?"
        params.append(to_utc_iso(now - lookback))
    rows = db.conn.execute(sql + " ORDER BY sc.ts_utc", params).fetchall()
    for row in rows:
        origin = classify_row(db, row, calls_since)
        db.conn.execute(
            "INSERT OR REPLACE INTO change_origin(state_change_id, origin, via, actor, automation) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["id"], origin.origin, origin.via, origin.actor, origin.automation))
    db.conn.commit()
    return len(rows)
