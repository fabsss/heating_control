from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(dt: datetime) -> str:
    """UTC-ISO-8601 mit festen Mikrosekunden; Textvergleich entspricht dem Zeitvergleich."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def attrs_json(attrs: dict[str, Any]) -> str:
    return json.dumps(attrs, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def attrs_hash(attrs: dict[str, Any]) -> str:
    return hashlib.sha1(attrs_json(attrs).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class EntityInfo:
    entity_id: str
    device_class: str | None = None
    area: str | None = None

    @property
    def ha_domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


@dataclass(frozen=True)
class EntityRule:
    entity_id: str
    kind: str
    domain: str
    attrs: tuple[str, ...] = ()
    change_attrs: tuple[str, ...] = ()
    delta_attrs: dict[str, float] = field(default_factory=dict)
    numeric_state_delta: float | None = None
    heartbeat_s: int | None = None
    debounce_s: int | None = None


@dataclass(frozen=True)
class Change:
    ts: datetime
    entity_id: str
    state: str | None
    attrs: dict[str, Any] = field(default_factory=dict)
    context_id: str | None = None
    context_user_id: str | None = None
    context_parent_id: str | None = None
