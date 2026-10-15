from __future__ import annotations

import json
from datetime import datetime

from collector.db import Database
from collector.models import parse_utc

from .config import AnalysisConfig
from .models import Intervention


def excluded(cfg: AnalysisConfig, room: str, ts: datetime) -> bool:
    return any(item.room == room and item.start <= ts and (item.end is None or ts < item.end)
               for item in cfg.exclusions)


def _kind_and_value(changed: set[str], new: dict) -> tuple[str, float | str] | None:
    if "temperature" in changed and new.get("temperature") is not None:
        try:
            return "setpoint", float(new["temperature"])
        except (TypeError, ValueError):
            return None
    if "preset_mode" in changed and new.get("preset_mode") is not None:
        return "preset", str(new["preset_mode"])
    return None


def load_interventions(db: Database, cfg: AnalysisConfig, *, start: datetime | None = None,
                       end: datetime | None = None) -> list[Intervention]:
    rows = db.conn.execute(
        "SELECT ts_utc, room, entity_id, changed_attrs, new, actor_user_id FROM manual_actions "
        "WHERE actor_class IN ('manual', 'device') ORDER BY entity_id, ts_utc")
    merged: list[Intervention] = []
    last_index: dict[str, int] = {}
    last_row_ts: dict[str, datetime] = {}
    for row in rows:
        if row["room"] is None:
            continue
        ts = parse_utc(row["ts_utc"])
        if (start is not None and ts < start) or (end is not None and ts >= end):
            continue
        if excluded(cfg, row["room"], ts):
            continue
        found = _kind_and_value(set(row["changed_attrs"].split(",")), json.loads(row["new"]))
        if found is None:
            continue
        entity_id = row["entity_id"]
        previous = last_row_ts.get(entity_id)
        if previous is not None and (ts - previous).total_seconds() <= cfg.merge_interventions_s:
            first = merged[last_index[entity_id]]
            merged[last_index[entity_id]] = Intervention(
                first.ts, first.room, first.entity_id, found[0], found[1], row["actor_user_id"])
        else:
            merged.append(Intervention(ts, row["room"], entity_id, found[0], found[1], row["actor_user_id"]))
            last_index[entity_id] = len(merged) - 1
        last_row_ts[entity_id] = ts
    return sorted(merged, key=lambda item: item.ts)
