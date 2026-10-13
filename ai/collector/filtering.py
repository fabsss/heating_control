from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import Change, EntityRule

EPSILON = 1e-9


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ChangeFilter:
    """Entscheidet pro Entität, welche Zustandsänderungen gespeichert werden.

    Verglichen wird immer mit der zuletzt *gespeicherten* Änderung, damit langsame
    Drifts nicht verloren gehen. Mit `debounce_s` wird ein Wert erst gespeichert,
    wenn er mindestens so lange gehalten wurde.
    """

    def __init__(self, rule: EntityRule, last: Change | None = None):
        self.rule = rule
        self.last: Change | None = self._trim(last) if last is not None else None
        self.pending: Change | None = None

    def _trim(self, change: Change) -> Change:
        attrs = {name: change.attrs[name] for name in self.rule.attrs if name in change.attrs}
        return Change(change.ts, change.entity_id, change.state, attrs,
                      change.context_id, change.context_user_id, change.context_parent_id)

    def _differs(self, new: Change) -> bool:
        last = self.last
        if last is None:
            return True
        rule = self.rule
        if rule.numeric_state_delta is not None:
            old_value, new_value = _num(last.state), _num(new.state)
            if old_value is not None and new_value is not None:
                if abs(new_value - old_value) >= rule.numeric_state_delta - EPSILON:
                    return True
            elif last.state != new.state:
                return True
        elif last.state != new.state:
            return True
        for name in rule.change_attrs:
            if last.attrs.get(name) != new.attrs.get(name):
                return True
        for name, delta in rule.delta_attrs.items():
            old_value, new_value = _num(last.attrs.get(name)), _num(new.attrs.get(name))
            if old_value is not None and new_value is not None:
                if abs(new_value - old_value) >= delta - EPSILON:
                    return True
            elif last.attrs.get(name) != new.attrs.get(name):
                return True
        if rule.heartbeat_s is not None and (new.ts - last.ts).total_seconds() >= rule.heartbeat_s:
            return True
        return False

    def feed(self, change: Change) -> list[Change]:
        if self.rule.debounce_s:
            confirmed = self._confirm(change.ts)
            self.pending = self._trim(change) if self._differs(change) else None
            return confirmed
        if self._differs(change):
            self.last = self._trim(change)
            return [self.last]
        return []

    def tick(self, now: datetime) -> list[Change]:
        return self._confirm(now) if self.rule.debounce_s else []

    def flush_pending(self) -> list[Change]:
        """Bestätigt einen wartenden Wert sofort (Ende eines Datenstroms)."""
        pending, self.pending = self.pending, None
        if pending is not None and self._differs(pending):
            self.last = pending
            return [pending]
        return []

    def _confirm(self, now: datetime) -> list[Change]:
        pending = self.pending
        if pending is None or (now - pending.ts).total_seconds() < self.rule.debounce_s:
            return []
        return self.flush_pending()
