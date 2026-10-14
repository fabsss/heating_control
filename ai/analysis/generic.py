from __future__ import annotations

from typing import Any

from .signals import SignalInfo

OFFSET_LIMIT = 120                   # Minuten
PREV_LIMIT = 7 * 24 * 60             # Minuten, eine Woche


def generic_vocabulary_text() -> str:
    return """Erlaubte Bausteine. Verwende ausschließlich diese, nichts anderes. Signale (sig_NN) stehen im Signalkatalog der Nachricht.

Auslöser ("trigger"), genau einer:
- {"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"}
    Wochentage (0 = Montag bis 6 = Sonntag) und Uhrzeit in lokaler Zeit.
- {"type": "signal", "signal": "sig_03", "event": "to_state", "value": "home", "min_prev_min": 180, "max_prev_min": null, "offset_min": 0}
    Ein Signal (Art Kategorie) wechselt in den Zustand value. Optional: der Zustand davor galt mindestens
    min_prev_min und höchstens (ausschließlich) max_prev_min Minuten. offset_min verschiebt den Zeitpunkt
    (0 bis 120 Minuten nach dem Ereignis).
- {"type": "signal", "signal": "sig_07", "event": "rises_above", "value": 18.5, "offset_min": 0}
- {"type": "signal", "signal": "sig_07", "event": "falls_below", "value": 12, "offset_min": 0}
    Ein Signal (Art Zahl) steigt über beziehungsweise fällt unter die Schwelle.
- {"type": "signal", "signal": "sig_09", "event": "alarm_time", "offset_min": -30}
    Die Weckzeit eines Signals (Art Zeitstempel) ist erreicht; offset_min darf hier auch negativ sein (bis -120),
    weil die Weckzeit im Voraus bekannt ist.

Bedingungen ("conditions", optional, höchstens zwei):
- {"type": "weekdays", "weekdays": [0, 1, 2, 3, 4]}
- {"type": "time_window", "from": "16:00", "to": "20:00"}   (from muss vor to liegen)
- {"type": "signal_state", "signal": "sig_03", "op": "is", "value": "home"}   ("is" oder "is_not", Art Kategorie)
- {"type": "signal_range", "signal": "sig_07", "op": "below", "value": 8}   ("below" oder "above", Art Zahl)

Aktion ("action"), genau eine:
- {"type": "preset", "value": "<Preset aus den Daten>"}
- {"type": "setpoint", "value": 21.0}   (Sollwert in Grad Celsius, 5 bis 30)"""


def _int(value: Any, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} {value!r} ist keine ganze Zahl")
    if not low <= value <= high:
        raise ValueError(f"{what} {value} liegt außerhalb von {low} bis {high}")
    return value


def _threshold(value: Any, info: SignalInfo) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Schwelle {value!r} ist keine Zahl")
    if not info.low <= value <= info.high:
        raise ValueError(f"Schwelle {value} liegt außerhalb des beobachteten Bereichs {info.low:g} bis {info.high:g}")
    return float(value)


def _signal(raw: dict, signals: dict[str, SignalInfo], kind: str) -> SignalInfo:
    handle = raw.get("signal")
    info = signals.get(handle) if isinstance(handle, str) else None
    if info is None:
        raise ValueError(f"unbekanntes Signal {handle!r}")
    if info.value_kind != kind:
        raise ValueError(f"Signal {handle} hat die Art {info.value_kind}, hier ist {kind} nötig")
    return info


def _category(raw: dict, info: SignalInfo) -> str:
    value = raw.get("value")
    if not isinstance(value, str) or value not in info.values:
        raise ValueError(f"Zustand {value!r} ist für {info.handle} unbekannt (bekannt: {', '.join(info.values)})")
    return value


def parse_signal_trigger(raw: dict, signals: dict[str, SignalInfo]) -> dict:
    event = raw.get("event")
    if event == "to_state":
        info = _signal(raw, signals, "categorical")
        trigger: dict = {"type": "signal", "signal": info.handle, "event": event, "value": _category(raw, info)}
        low = raw.get("min_prev_min")
        high = raw.get("max_prev_min")
        if low is not None:
            trigger["min_prev_min"] = _int(low, 0, PREV_LIMIT, "min_prev_min")
        if high is not None:
            trigger["max_prev_min"] = _int(high, 1, PREV_LIMIT, "max_prev_min")
        if low is not None and high is not None and low >= high:
            raise ValueError("min_prev_min muss kleiner als max_prev_min sein")
    elif event in ("rises_above", "falls_below"):
        info = _signal(raw, signals, "numeric")
        trigger = {"type": "signal", "signal": info.handle, "event": event, "value": _threshold(raw.get("value"), info)}
    elif event == "alarm_time":
        info = _signal(raw, signals, "timestamp")
        trigger = {"type": "signal", "signal": info.handle, "event": event}
    else:
        raise ValueError(f"unbekanntes Signal-Ereignis {event!r}")
    offset = _int(raw.get("offset_min", 0), -OFFSET_LIMIT, OFFSET_LIMIT, "offset_min")
    if offset < 0 and event != "alarm_time":
        raise ValueError("Ein negativer Versatz ist nur bei alarm_time möglich, nur dort ist das Ereignis im Voraus bekannt")
    trigger["offset_min"] = offset
    return trigger


def parse_signal_condition(raw: dict, signals: dict[str, SignalInfo]) -> dict:
    kind = raw.get("type")
    if kind == "signal_state":
        info = _signal(raw, signals, "categorical")
        if raw.get("op") not in ("is", "is_not"):
            raise ValueError(f"signal_state: op {raw.get('op')!r} ist ungültig")
        return {"type": kind, "signal": info.handle, "op": raw["op"], "value": _category(raw, info)}
    info = _signal(raw, signals, "numeric")
    if raw.get("op") not in ("below", "above"):
        raise ValueError(f"signal_range: op {raw.get('op')!r} ist ungültig")
    return {"type": "signal_range", "signal": info.handle, "op": raw["op"], "value": _threshold(raw.get("value"), info)}
