from __future__ import annotations

from datetime import datetime

from .db import Database
from .filtering import ChangeFilter
from .models import Change, EntityRule


class Pipeline:
    """Hält pro Entität einen ChangeFilter und puffert gespeicherte Änderungen."""

    def __init__(self, db: Database):
        self.db = db
        self.rules: dict[str, EntityRule] = {}
        self._filters: dict[str, ChangeFilter] = {}
        self._buffer: list[tuple[Change, str]] = []

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def set_rules(self, rules: dict[str, EntityRule]) -> None:
        for entity_id in list(self._filters):
            if entity_id not in rules:
                del self._filters[entity_id]
        for entity_id, rule in rules.items():
            if entity_id not in self._filters or self.rules.get(entity_id) != rule:
                self._filters[entity_id] = ChangeFilter(rule, self.db.last_change(entity_id))
        self.rules = dict(rules)

    def handle(self, change: Change, source: str = "live") -> None:
        change_filter = self._filters.get(change.entity_id)
        if change_filter is None:
            return
        for stored in change_filter.feed(change):
            self._buffer.append((stored, source))

    def tick(self, now: datetime) -> None:
        for change_filter in self._filters.values():
            for stored in change_filter.tick(now):
                self._buffer.append((stored, "live"))

    def flush(self) -> int:
        if not self._buffer:
            return 0
        by_source: dict[str, list[Change]] = {}
        for change, source in self._buffer:
            by_source.setdefault(source, []).append(change)
        inserted = 0
        for source, changes in by_source.items():
            inserted += self.db.insert_changes(changes, source)
        self._buffer.clear()  # erst nach erfolgreichem Schreiben; Duplikate fängt UNIQUE ab
        return inserted
