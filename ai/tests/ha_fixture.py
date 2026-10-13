from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from collector.ids import ulid_to_bytes

CTX = "01M4TXPBEYE60APXTRFD3PKB80"
PARENT = "01M4TXPBESW8FDM38QJVTXJZS9"
USER_FABIAN = "45171d305dd5422ca9d0ecf755abbda7"
USER_SVENJA = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"


def build_ha_db(path: Path, rows: list[dict], events: list[dict] | None = None,
                schema_version: int = 48) -> Path:
    """Baut eine minimale home-assistant_v2.db im aktuellen Schema (Zeitstempel und *_bin-Spalten)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_changes(change_id INTEGER PRIMARY KEY, schema_version INTEGER, changed TEXT);
        CREATE TABLE states_meta(metadata_id INTEGER PRIMARY KEY, entity_id TEXT);
        CREATE TABLE state_attributes(attributes_id INTEGER PRIMARY KEY, shared_attrs TEXT);
        CREATE TABLE states(
          state_id INTEGER PRIMARY KEY, metadata_id INTEGER, state TEXT, attributes_id INTEGER,
          last_updated_ts REAL, context_id_bin BLOB, context_user_id_bin BLOB, context_parent_id_bin BLOB);
        CREATE TABLE event_types(event_type_id INTEGER PRIMARY KEY, event_type TEXT);
        CREATE TABLE event_data(data_id INTEGER PRIMARY KEY, shared_data TEXT);
        CREATE TABLE events(event_id INTEGER PRIMARY KEY, event_type_id INTEGER, data_id INTEGER, time_fired_ts REAL);
        """
    )
    conn.execute("INSERT INTO schema_changes(schema_version, changed) VALUES (?, 'now')", (schema_version,))
    meta: dict[str, int] = {}
    for row in rows:
        entity_id = row["entity_id"]
        if entity_id not in meta:
            meta[entity_id] = conn.execute(
                "INSERT INTO states_meta(entity_id) VALUES (?)", (entity_id,)
            ).lastrowid
        attributes_id = None
        if row.get("attrs") is not None:
            attributes_id = conn.execute(
                "INSERT INTO state_attributes(shared_attrs) VALUES (?)", (json.dumps(row["attrs"]),)
            ).lastrowid
        conn.execute(
            "INSERT INTO states(metadata_id, state, attributes_id, last_updated_ts, context_id_bin, "
            "context_user_id_bin, context_parent_id_bin) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                meta[entity_id], row["state"], attributes_id, row["ts"],
                ulid_to_bytes(row["ctx"]) if row.get("ctx") else None,
                bytes.fromhex(row["user"]) if row.get("user") else None,
                ulid_to_bytes(row["parent"]) if row.get("parent") else None,
            ),
        )
    if events:
        type_id = conn.execute(
            "INSERT INTO event_types(event_type) VALUES ('thermostat_actor_logged')"
        ).lastrowid
        for event in events:
            data_id = conn.execute(
                "INSERT INTO event_data(shared_data) VALUES (?)", (json.dumps(event["data"]),)
            ).lastrowid
            conn.execute(
                "INSERT INTO events(event_type_id, data_id, time_fired_ts) VALUES (?, ?, ?)",
                (type_id, data_id, event["ts"]),
            )
    conn.commit()
    conn.close()
    return path
