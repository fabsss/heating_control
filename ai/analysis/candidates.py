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


def _signal_trigger_text(trigger: dict, signals: dict[str, str]) -> str:
    name = signals.get(trigger["signal"], trigger["signal"])
    event = trigger["event"]
    if event == "to_state":
        text = f"{name} wechselt zu {trigger['value']}"
        low, high = trigger.get("min_prev_min"), trigger.get("max_prev_min")
        if low is not None or high is not None:
            text += f" (vorher {low or 0}–{high if high is not None else '∞'} min im anderen Zustand)"
    elif event == "rises_above":
        text = f"{name} steigt über {trigger['value']:g}"
    elif event == "falls_below":
        text = f"{name} fällt unter {trigger['value']:g}"
    else:
        text = f"{name}: Weckzeit erreicht"
    offset = trigger.get("offset_min", 0)
    return text + (f", {offset:+d} min" if offset else "")


def _trigger_text(trigger: dict, pseudo: Pseudonyms, signals: dict[str, str] | None = None) -> str:
    if trigger["type"] == "time":
        return f"{weekday_label(trigger['weekdays'])} {trigger['time']}"
    if trigger["type"] == "signal":
        return _signal_trigger_text(trigger, signals or {})
    event = trigger["event"]
    if event == "person_arrives":
        return f"{pseudo.display_name(trigger['person'])} kommt heim"
    if event == "person_leaves":
        return f"{pseudo.display_name(trigger['person'])} geht"
    if event == "flat_state":
        return f"Wohnungszustand wird {trigger['value']}"
    return {"bed_in": "jemand legt sich ins Bett", "bed_out": "jemand steht auf",
            "window_closed": "Fenster geschlossen", "office_day_start": "Büro-Tag beginnt"}[event]


def _condition_text(condition: dict, pseudo: Pseudonyms, signals: dict[str, str] | None = None) -> str:
    kind = condition["type"]
    if kind in ("signal_state", "signal_range"):
        name = (signals or {}).get(condition["signal"], condition["signal"])
        if kind == "signal_state":
            return f"{name} ist {'' if condition['op'] == 'is' else 'nicht '}{condition['value']}"
        return f"{name} {'unter' if condition['op'] == 'below' else 'über'} {condition['value']:g}"
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


def describe(candidate: Candidate, cfg: AnalysisConfig, pseudo: Pseudonyms,
             signals: dict[str, str] | None = None) -> tuple[str, str, str]:
    """Lesbarer Text für die lokale Anzeige (mit Klarnamen, geht nie an das Modell)."""
    room = room_name(cfg, candidate.room)
    try:
        when = _trigger_text(candidate.trigger, pseudo, signals)
        if candidate.conditions:
            when += " wenn " + "; ".join(_condition_text(item, pseudo, signals) for item in candidate.conditions)
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
        elif trigger["type"] == "signal":
            key = ("signal", trigger["signal"], trigger["event"], str(trigger.get("value", "")),
                   trigger.get("min_prev_min"), trigger.get("offset_min", 0) // 30)
        else:
            key = ("event", trigger["event"], trigger.get("person") or trigger.get("value") or "")
        action = candidate.action
        value = action["value"] if action["type"] == "preset" else round(float(action["value"]) * 2) / 2
        return (candidate.room, key, action["type"], value)
    except (KeyError, TypeError, ValueError):
        return None
