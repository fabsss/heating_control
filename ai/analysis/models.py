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
class Association:
    """Ergebnis des Scans: Um dieses Ereignis herum liegen auffällig oft Eingriffe."""

    signal: str                  # sig_NN
    event: str                   # to_state | rises_above | falls_below | alarm_time
    value: str | None            # Zustand beziehungsweise Schwelle
    prev_class: str              # "alle" oder Dauerklasse des vorherigen Zustands, z. B. "≥180"
    room: str | None             # area_id, None = alle Räume
    win_start: int               # Minuten relativ zum Ereignis
    win_end: int
    events: int
    hits: int
    control_events: int
    control_hits: int
    lift: float
    p: float
    q: float
    typical_action: str = ""


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
