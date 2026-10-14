from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

from .config import Config, ConfigError
from .db import Database
from .filtering import ChangeFilter
from .ids import bytes_to_hex, bytes_to_ulid
from .models import Change, EntityInfo, EntityRule, to_utc_iso, utcnow
from .rules import resolve_rule

CHUNK = 500
BATCH = 5000
REQUIRED_STATE_COLUMNS = {
    "state_id", "metadata_id", "state", "attributes_id", "last_updated_ts",
    "context_id_bin", "context_user_id_bin", "context_parent_id_bin",
}
REQUIRED_TABLES = {"states", "states_meta", "state_attributes"}
HISTORY_KEYS = {"entity_id", "room", "role", "domain", "kind", "valid_from", "valid_to"}


class UnsupportedSchema(Exception):
    pass


class CorruptDatabase(Exception):
    pass


@dataclass
class BackfillResult:
    rows_read: int
    rows_inserted: int
    first_ts: str | None
    last_ts: str | None
    rows_enriched: int = 0


@dataclass(frozen=True)
class HistoryMapEntry:
    entity_id: str
    room: str
    role: str = "climate"
    domain: str = "heating"
    kind: str = "climate"
    valid_from: str | None = None
    valid_to: str | None = None


def load_history_map(path: str | Path) -> list[HistoryMapEntry]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = []
    for item in raw.get("entities", []):
        unknown = set(item) - HISTORY_KEYS
        if unknown:
            raise ConfigError(f"history_map: unbekannte Schlüssel {sorted(unknown)} bei {item.get('entity_id')}")
        entries.append(HistoryMapEntry(**item))
    return entries


def history_rules(entries: Iterable[HistoryMapEntry], cfg: Config,
                  labeled_ids: set[str]) -> tuple[dict[str, EntityRule], dict[str, str]]:
    rules: dict[str, EntityRule] = {}
    rooms: dict[str, str] = {}
    for entry in entries:
        if entry.entity_id in labeled_ids or entry.entity_id in rules:
            continue
        info = EntityInfo(entry.entity_id, None, entry.room)
        rules[entry.entity_id] = resolve_rule(info, cfg, override={"kind": entry.kind, "domain": entry.domain})
        rooms[entry.entity_id] = entry.room
    return rules, rooms


def open_readonly(path: str | Path) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def check_ha_schema(conn: sqlite3.Connection) -> None:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    columns = {row[1] for row in conn.execute("PRAGMA table_info(states)")}
    missing_tables = REQUIRED_TABLES - tables
    missing_columns = REQUIRED_STATE_COLUMNS - columns
    if missing_tables or missing_columns:
        version = None
        if "schema_changes" in tables:
            found = conn.execute(
                "SELECT schema_version FROM schema_changes ORDER BY change_id DESC LIMIT 1").fetchone()
            version = found[0] if found else None
        raise UnsupportedSchema(
            f"Unbekanntes Schema der Home-Assistant-Datenbank (Schemaversion {version}). "
            f"Es fehlen Tabellen {sorted(missing_tables)} und Spalten {sorted(missing_columns)}. "
            "Unterstützt wird das aktuelle Schema mit Zeitstempel- und *_bin-Spalten."
        )


def verify_integrity(conn: sqlite3.Connection) -> None:
    answer = conn.execute("PRAGMA quick_check(1)").fetchone()[0]
    if answer != "ok":
        raise CorruptDatabase(f"Die Datenbank ist beschädigt (quick_check: {answer}). "
                              "-wal und -shm mitkopieren oder einen anderen Snapshot nehmen.")


def _query_rows(conn: sqlite3.Connection, entity_ids: list[str],
                since_ts: float | None, until_ts: float | None) -> Iterable[sqlite3.Row]:
    placeholders = ",".join("?" for _ in entity_ids)
    params: list = list(entity_ids)
    time_filter = ""
    if since_ts is not None:
        time_filter += " AND s.last_updated_ts >= ?"
        params.append(since_ts)
    if until_ts is not None:
        time_filter += " AND s.last_updated_ts < ?"
        params.append(until_ts)
    sql = f"""
        SELECT sm.entity_id AS entity_id, s.state AS state, s.last_updated_ts AS ts,
               sa.shared_attrs AS attrs, s.context_id_bin AS ctx,
               s.context_user_id_bin AS usr, s.context_parent_id_bin AS parent
        FROM states s
        JOIN states_meta sm ON sm.metadata_id = s.metadata_id
        LEFT JOIN state_attributes sa ON sa.attributes_id = s.attributes_id
        WHERE sm.entity_id IN ({placeholders}){time_filter}
        ORDER BY s.last_updated_ts, s.state_id
    """
    return conn.execute(sql, params)


def _row_to_change(row: sqlite3.Row) -> Change:
    try:
        attrs = json.loads(row["attrs"]) if row["attrs"] else {}
    except ValueError:
        attrs = {}
    return Change(
        ts=datetime.fromtimestamp(row["ts"], tz=timezone.utc),
        entity_id=row["entity_id"],
        state=row["state"],
        attrs=attrs,
        context_id=bytes_to_ulid(row["ctx"]) if row["ctx"] else None,
        context_user_id=bytes_to_hex(row["usr"]) if row["usr"] else None,
        context_parent_id=bytes_to_ulid(row["parent"]) if row["parent"] else None,
    )


def backfill_ha_db(ha_db_path: str | Path, db: Database, rules: dict[str, EntityRule],
                   rooms: dict[str, str | None], *, history_only: frozenset[str] | set[str] = frozenset(),
                   since: datetime | None = None, until: datetime | None = None,
                   now: datetime | None = None, check_integrity: bool = True) -> BackfillResult:
    started = now or utcnow()
    source = f"backfill:{Path(ha_db_path).name}"
    src = open_readonly(ha_db_path)
    try:
        check_ha_schema(src)
        if check_integrity:
            verify_integrity(src)
        for entity_id, rule in rules.items():
            db.upsert_entity(rule, rooms.get(entity_id), started)
            if entity_id not in history_only and rooms.get(entity_id):
                db.extend_room_to_beginning(entity_id, rooms[entity_id], rule.kind)
        filters = {entity_id: ChangeFilter(rule) for entity_id, rule in rules.items()}
        since_ts = since.timestamp() if since else None
        until_ts = until.timestamp() if until else None
        enriched_before = db.enriched
        rows_read = inserted = 0
        first_ts = last_ts = None
        last_by_entity: dict[str, datetime] = {}
        batch: list[Change] = []
        entity_ids = sorted(rules)
        try:
            for start in range(0, len(entity_ids), CHUNK):
                for row in _query_rows(src, entity_ids[start:start + CHUNK], since_ts, until_ts):
                    if row["state"] is None or row["ts"] is None:
                        continue
                    rows_read += 1
                    change = _row_to_change(row)
                    last_by_entity[change.entity_id] = change.ts
                    stamp = to_utc_iso(change.ts)
                    first_ts = stamp if first_ts is None or stamp < first_ts else first_ts
                    last_ts = stamp if last_ts is None or stamp > last_ts else last_ts
                    batch.extend(filters[change.entity_id].feed(change))
                    if len(batch) >= BATCH:
                        inserted += db.insert_changes(batch, source, commit=False)
                        batch = []
            for change_filter in filters.values():
                batch.extend(change_filter.flush_pending())
            inserted += db.insert_changes(batch, source, commit=False)
            db.commit()
        except Exception:
            db.rollback()
            raise
        for entity_id in history_only:
            db.mark_inactive(entity_id, last_by_entity.get(entity_id) or started)
        db.record_run(source, first_ts, last_ts, inserted, "ok", to_utc_iso(started))
        return BackfillResult(rows_read, inserted, first_ts, last_ts, db.enriched - enriched_before)
    finally:
        src.close()


def backfill_with_map(cfg: Config, db: Database, infos: list[EntityInfo],
                      entries: list[HistoryMapEntry], ha_db_path: str | Path, *,
                      since: datetime | None = None, until: datetime | None = None,
                      check_integrity: bool = True) -> BackfillResult:
    """Gelabelte Entitäten plus Einträge der history_map (nur Vergangenes)."""
    rules = {info.entity_id: resolve_rule(info, cfg) for info in infos}
    rooms: dict[str, str | None] = {info.entity_id: info.area for info in infos}
    extra_rules, extra_rooms = history_rules(entries, cfg, set(rules))
    for entry in entries:
        db.add_history_room(entry.room, entry.entity_id, entry.role, entry.valid_from, entry.valid_to)
    rules.update(extra_rules)
    rooms.update(extra_rooms)
    return backfill_ha_db(ha_db_path, db, rules, rooms, history_only=set(extra_rules),
                          since=since, until=until, check_integrity=check_integrity)
