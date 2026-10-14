from __future__ import annotations

import json

from .config import AnalysisConfig
from .features import room_name
from .models import STATUS_UNTESTABLE, Candidate
from .pseudonym import Pseudonyms

SHORT_DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def weekday_label(days: list[int]) -> str:
    days = sorted(days)
    if days == list(range(7)):
        return "täglich"
    if days == [0, 1, 2, 3, 4]:
        return "Mo–Fr"
    if days == [5, 6]:
        return "Sa–So"
    return ",".join(SHORT_DAYS[day] for day in days)


def _trigger_text(trigger: dict, pseudo: Pseudonyms) -> str:
    if trigger["type"] == "time":
        return f"{weekday_label(trigger['weekdays'])} {trigger['time']}"
    event = trigger["event"]
    if event == "person_arrives":
        return f"{pseudo.display_name(trigger['person'])} kommt heim"
    if event == "person_leaves":
        return f"{pseudo.display_name(trigger['person'])} geht"
    if event == "flat_state":
        return f"Wohnungszustand wird {trigger['value']}"
    return {"bed_in": "jemand legt sich ins Bett", "bed_out": "jemand steht auf",
            "window_closed": "Fenster geschlossen", "office_day_start": "Büro-Tag beginnt"}[event]


def _condition_text(condition: dict, pseudo: Pseudonyms) -> str:
    kind = condition["type"]
    if kind == "weekdays":
        return weekday_label(condition["weekdays"])
    if kind == "time_window":
        return f"{condition['from']}–{condition['to']} Uhr"
    if kind == "outside_temp":
        word = "unter" if condition["op"] == "below" else "über"
        return f"Außentemperatur {word} {condition['value']:g} °C"
    state = "ist zu Hause" if condition["home"] else "ist nicht zu Hause"
    return f"{pseudo.display_name(condition['person'])} {state}"


def _action_text(action: dict) -> str:
    if action["type"] == "preset":
        return f"Preset {action['value']}"
    return f"{action['value']:g} °C"


def describe(candidate: Candidate, cfg: AnalysisConfig, pseudo: Pseudonyms) -> tuple[str, str, str]:
    """Lesbarer Text für die lokale Anzeige (mit Klarnamen, geht nie an das Modell)."""
    room = room_name(cfg, candidate.room)
    try:
        when = _trigger_text(candidate.trigger, pseudo)
        if candidate.conditions:
            when += " wenn " + "; ".join(_condition_text(item, pseudo) for item in candidate.conditions)
        return room, when, _action_text(candidate.action)
    except (KeyError, TypeError, ValueError):
        return (room, json.dumps(candidate.trigger, ensure_ascii=False),
                json.dumps(candidate.action, ensure_ascii=False))


def signature(candidate: Candidate) -> tuple | None:
    """Vergleichsschlüssel für `compare`: gleicher Raum, gleiche Aktion, im Kern gleicher Auslöser."""
    if candidate.status == STATUS_UNTESTABLE:
        return None
    try:
        trigger = candidate.trigger
        if trigger["type"] == "time":
            hour, minute = (int(part) for part in trigger["time"].split(":"))
            key: tuple = ("time", tuple(sorted(trigger["weekdays"])), (hour * 60 + minute) // 30)
        else:
            key = ("event", trigger["event"], trigger.get("person") or trigger.get("value") or "")
        action = candidate.action
        value = action["value"] if action["type"] == "preset" else round(float(action["value"]) * 2) / 2
        return (candidate.room, key, action["type"], value)
    except (KeyError, TypeError, ValueError):
        return None
