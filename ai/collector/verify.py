from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .backfill import open_readonly
from .db import Database
from .models import parse_utc

EVENTS_SQL = """
SELECT e.time_fired_ts AS ts, ed.shared_data AS data
FROM events e
JOIN event_types et ON et.event_type_id = e.event_type_id
JOIN event_data ed ON ed.data_id = e.data_id
WHERE et.event_type = 'thermostat_actor_logged'
ORDER BY e.time_fired_ts
"""


@dataclass
class VerifyReport:
    logged_manual: int = 0
    matched: int = 0
    untracked_entity: int = 0
    unmatched: list[dict] = field(default_factory=list)
    extra_manual: int = 0

    @property
    def match_rate(self) -> float:
        considered = self.logged_manual - self.untracked_entity
        return 1.0 if considered <= 0 else self.matched / considered


def verify_logged_events(ha_db_path: str | Path, db: Database, tolerance_s: float = 5.0) -> VerifyReport:
    src = open_readonly(ha_db_path)
    try:
        events = src.execute(EVENTS_SQL).fetchall()
    finally:
        src.close()
    tracked = {row["entity_id"] for row in db.conn.execute("SELECT entity_id FROM entities")}
    manual = db.conn.execute(
        "SELECT ts_utc, entity_id, changed_attrs, actor_user_id FROM manual_actions "
        "WHERE actor_class = 'manual' ORDER BY ts_utc"
    ).fetchall()
    by_entity: dict[str, list[tuple[int, datetime, set[str], str | None]]] = defaultdict(list)
    for index, row in enumerate(manual):
        by_entity[row["entity_id"]].append(
            (index, parse_utc(row["ts_utc"]), set(row["changed_attrs"].split(",")), row["actor_user_id"])
        )

    report = VerifyReport()
    used: set[int] = set()
    for event in events:
        data = json.loads(event["data"])
        actor = data.get("actor_id")
        if not actor or actor == "unknown":
            continue
        report.logged_manual += 1
        entity_id = data["entity_id"]
        if entity_id not in tracked:
            report.untracked_entity += 1
            continue
        wanted = set()
        change_types = data.get("change_types") or []
        if "setpoint" in change_types:
            wanted.add("temperature")
        if "hvac_mode" in change_types:
            wanted.add("hvac_mode")
        fired = datetime.fromtimestamp(event["ts"], tz=timezone.utc)
        hit = next(
            (index for index, ts, changed, user in by_entity[entity_id]
             if index not in used and user == actor and changed & wanted
             and abs((ts - fired).total_seconds()) <= tolerance_s),
            None,
        )
        if hit is None:
            report.unmatched.append({"entity_id": entity_id, "ts": fired.isoformat(),
                                     "change_types": change_types, "actor_id": actor})
        else:
            used.add(hit)
            report.matched += 1
    report.extra_manual = len(manual) - len(used)
    return report
