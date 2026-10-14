from __future__ import annotations

from bisect import bisect_right
from datetime import datetime

from collector.db import Database
from collector.models import parse_utc


class Timeline:
    """Zustandsverlauf einer Entität: welcher Zustand galt zum Zeitpunkt t?"""

    def __init__(self, points: list[tuple[datetime, str | None]] | None = None):
        self._points = sorted(points or [], key=lambda point: point[0])
        self._times = [point[0] for point in self._points]

    def __bool__(self) -> bool:
        return bool(self._points)

    def value_at(self, ts: datetime) -> str | None:
        index = bisect_right(self._times, ts) - 1
        return None if index < 0 else self._points[index][1]

    def numeric_at(self, ts: datetime) -> float | None:
        value = self.value_at(ts)
        try:
            return None if value is None else float(value)
        except ValueError:
            return None

    def transitions(self) -> list[tuple[datetime, str | None, str | None]]:
        return [(current[0], previous[1], current[1])
                for previous, current in zip(self._points, self._points[1:])
                if previous[1] != current[1]]

    def states(self) -> set[str]:
        return {state for _, state in self._points if state is not None}


def load_timeline(db: Database, entity_id: str) -> Timeline:
    rows = db.conn.execute(
        "SELECT ts_utc, state FROM state_changes WHERE entity_id = ? ORDER BY ts_utc, id", (entity_id,))
    return Timeline([(parse_utc(row["ts_utc"]), row["state"]) for row in rows])
