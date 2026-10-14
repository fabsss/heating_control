from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

STATUS_OPEN = "offen"
STATUS_PASSED = "bestanden"
STATUS_FEW = "zu_wenig_belege"
STATUS_REJECTED = "abgelehnt"
STATUS_UNTESTABLE = "nicht_pruefbar"


@dataclass(frozen=True)
class Intervention:
    """Ein manueller Eingriff in einen Raum (Sollwert oder Preset)."""

    ts: datetime                 # UTC
    room: str                    # area_id
    entity_id: str
    kind: str                    # "setpoint" | "preset"
    value: float | str
    user_id: str | None = None


@dataclass
class Candidate:
    room: str                    # area_id
    trigger: dict[str, Any]
    action: dict[str, Any]
    conditions: list[dict[str, Any]] = field(default_factory=list)
    hypothesis: str = ""
    status: str = STATUS_OPEN
    note: str = ""
    id: int | None = None
    run_id: int | None = None
