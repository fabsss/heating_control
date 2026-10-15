from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from jinja2.nativetypes import NativeEnvironment


def _as_timestamp(value: Any, default: Any = None) -> Any:
    if isinstance(value, datetime):
        return value.timestamp()
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(value)).timestamp()
        except ValueError:
            return default


class HaEnv:
    """Wertet HA-Templates mit Jinja2 aus (nur die hier benutzten Funktionen)."""

    def __init__(self, states: dict[str, str], attrs: dict[str, dict], now: datetime):
        self.states = states
        self.attrs = attrs
        self.now = now
        self.env = NativeEnvironment()
        self.env.globals.update(
            states=lambda entity: self.states.get(entity, "unknown"),
            state_attr=lambda entity, name: self.attrs.get(entity, {}).get(name),
            is_state=lambda entity, value: self.states.get(entity, "unknown") == value,
            now=lambda: self.now,
            as_timestamp=_as_timestamp,
        )
        self.env.filters["as_timestamp"] = _as_timestamp
        self.env.filters["to_json"] = lambda value: json.dumps(value, ensure_ascii=False)

    def render(self, template: Any, context: dict[str, Any]) -> Any:
        if not isinstance(template, str):
            return template
        return self.env.from_string(template).render(**context)

    def render_variables(self, block: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Rendert die Einträge der Reihe nach; spätere sehen die früheren (wie das HA-Schritt `variables`)."""
        local = dict(context)
        result: dict[str, Any] = {}
        for name, template in block.items():
            local[name] = result[name] = self.render(template, local)
        return result


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def minutes_ago(now: datetime, minutes: float) -> float:
    return (now - timedelta(minutes=minutes)).timestamp()
