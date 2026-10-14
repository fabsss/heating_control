from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import STATUS_OPEN, STATUS_UNTESTABLE, Candidate
from .pseudonym import Pseudonyms

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PERSON_EVENTS = ("person_arrives", "person_leaves")
PLAIN_EVENTS = ("bed_in", "bed_out", "window_closed", "office_day_start")
EVENT_NAMES = (*PERSON_EVENTS, "flat_state", *PLAIN_EVENTS)


@dataclass(frozen=True)
class ParseContext:
    rooms: dict[str, str]                 # Klartextname -> area_id
    pseudo: Pseudonyms
    presets: frozenset[str]
    flat_states: frozenset[str]
    min_setpoint: float = 5.0
    max_setpoint: float = 30.0


def vocabulary_text() -> str:
    return """Erlaubte Bausteine. Verwende ausschließlich diese, nichts anderes.

Auslöser ("trigger"), genau einer:
- {"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"}
    Wochentage (0 = Montag bis 6 = Sonntag) und Uhrzeit in lokaler Zeit.
- {"type": "event", "event": "person_arrives", "person": "person_2"}
    Eine Person kommt nach Hause.
- {"type": "event", "event": "person_leaves", "person": "person_2"}
    Eine Person verlässt die Wohnung.
- {"type": "event", "event": "flat_state", "value": "<Wohnungszustand aus den Daten>"}
    Der Wohnungszustand wechselt zu diesem Wert.
- {"type": "event", "event": "bed_in"} und {"type": "event", "event": "bed_out"}
    Jemand legt sich ins Bett beziehungsweise steht auf.
- {"type": "event", "event": "window_closed"}
    Alle Fenster des Raums sind geschlossen.
- {"type": "event", "event": "office_day_start"}
    Ein Büro-Tag beginnt.

Bedingungen ("conditions", optional, Liste):
- {"type": "weekdays", "weekdays": [0, 1, 2, 3, 4]}
- {"type": "time_window", "from": "16:00", "to": "20:00"}   (from muss vor to liegen)
- {"type": "outside_temp", "op": "below", "value": 8}   ("below" oder "above", Grad Celsius)
- {"type": "person_home", "person": "person_1", "home": true}

Aktion ("action"), genau eine:
- {"type": "preset", "value": "<Preset aus den Daten>"}
- {"type": "setpoint", "value": 21.0}   (Sollwert in Grad Celsius, 5 bis 30)"""


def _mapping(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{what} fehlt oder ist kein Objekt")
    return value


def _time(value: Any) -> str:
    if not isinstance(value, str) or not TIME_RE.match(value):
        raise ValueError(f"Uhrzeit {value!r} ist ungültig (erwartet HH:MM)")
    return value


def _weekdays(value: Any) -> list[int]:
    if not isinstance(value, list) or not value:
        raise ValueError("weekdays muss eine nicht leere Liste sein")
    days = []
    for day in value:
        if isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6:
            raise ValueError(f"Wochentag {day!r} ist ungültig (0 bis 6)")
        days.append(day)
    return sorted(set(days))


def _number(value: Any, low: float, high: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} {value!r} ist keine Zahl")
    if not low <= value <= high:
        raise ValueError(f"{what} {value} liegt außerhalb von {low} bis {high}")
    return float(value)


def _person(alias: Any, ctx: ParseContext) -> str:
    entity_id = ctx.pseudo.person_for_alias(alias) if isinstance(alias, str) else None
    if entity_id is None:
        raise ValueError(f"unbekannte Person {alias!r}")
    return entity_id


def _trigger(raw: Any, ctx: ParseContext) -> dict:
    raw = _mapping(raw, "Auslöser")
    kind = raw.get("type")
    if kind == "time":
        return {"type": "time", "weekdays": _weekdays(raw.get("weekdays")), "time": _time(raw.get("time"))}
    if kind == "event":
        event = raw.get("event")
        if event in PERSON_EVENTS:
            return {"type": "event", "event": event, "person": _person(raw.get("person"), ctx)}
        if event == "flat_state":
            value = raw.get("value")
            if not isinstance(value, str) or value not in ctx.flat_states:
                raise ValueError(f"unbekannter Wohnungszustand {value!r}")
            return {"type": "event", "event": event, "value": value}
        if event in PLAIN_EVENTS:
            return {"type": "event", "event": event}
        raise ValueError(f"unbekanntes Ereignis {event!r}")
    raise ValueError(f"unbekannter Auslöser {kind!r}")


def _condition(raw: Any, ctx: ParseContext) -> dict:
    raw = _mapping(raw, "Bedingung")
    kind = raw.get("type")
    if kind == "weekdays":
        return {"type": "weekdays", "weekdays": _weekdays(raw.get("weekdays"))}
    if kind == "time_window":
        start, end = _time(raw.get("from")), _time(raw.get("to"))
        if start >= end:
            raise ValueError("time_window: from muss vor to liegen")
        return {"type": "time_window", "from": start, "to": end}
    if kind == "outside_temp":
        if raw.get("op") not in ("below", "above"):
            raise ValueError(f"outside_temp: op {raw.get('op')!r} ist ungültig")
        return {"type": "outside_temp", "op": raw["op"], "value": _number(raw.get("value"), -40, 50, "Temperatur")}
    if kind == "person_home":
        if not isinstance(raw.get("home"), bool):
            raise ValueError("person_home: home muss true oder false sein")
        return {"type": "person_home", "person": _person(raw.get("person"), ctx), "home": raw["home"]}
    raise ValueError(f"unbekannte Bedingung {kind!r}")


def _action(raw: Any, ctx: ParseContext) -> dict:
    raw = _mapping(raw, "Aktion")
    kind = raw.get("type")
    if kind == "preset":
        if not isinstance(raw.get("value"), str) or raw["value"] not in ctx.presets:
            raise ValueError(f"unbekanntes Preset {raw.get('value')!r}")
        return {"type": "preset", "value": raw["value"]}
    if kind == "setpoint":
        return {"type": "setpoint", "value": _number(raw.get("value"), ctx.min_setpoint, ctx.max_setpoint, "Sollwert")}
    raise ValueError(f"unbekannte Aktion {kind!r}")


def parse_candidate(raw: Any, ctx: ParseContext) -> Candidate:
    """Prüft einen Kandidaten des Modells gegen das Vokabular. Wirft nie."""
    if not isinstance(raw, dict):
        return Candidate(room="", trigger={}, action={}, status=STATUS_UNTESTABLE,
                         note="Eintrag ist kein Objekt")
    hypothesis = str(raw.get("hypothesis") or "")[:500]
    name = raw.get("room")
    try:
        if not isinstance(name, str) or name not in ctx.rooms:
            raise ValueError(f"unbekannter Raum {name!r}")
        conditions_raw = raw.get("conditions") or []
        if not isinstance(conditions_raw, list):
            raise ValueError("conditions muss eine Liste sein")
        return Candidate(
            room=ctx.rooms[name],
            trigger=_trigger(raw.get("trigger"), ctx),
            conditions=[_condition(item, ctx) for item in conditions_raw],
            action=_action(raw.get("action"), ctx),
            hypothesis=hypothesis,
            status=STATUS_OPEN,
        )
    except ValueError as exc:
        trigger = raw.get("trigger") if isinstance(raw.get("trigger"), dict) else {}
        action = raw.get("action") if isinstance(raw.get("action"), dict) else {}
        return Candidate(room=name if isinstance(name, str) else "", trigger=trigger, action=action,
                         hypothesis=hypothesis, status=STATUS_UNTESTABLE, note=str(exc))


def parse_candidates(raws: list, ctx: ParseContext) -> list[Candidate]:
    return [parse_candidate(item, ctx) for item in raws]
