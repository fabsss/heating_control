from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from analysis.models import Candidate
from collector.db import Database

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
UNUSABLE = ["unavailable", "unknown"]
LEGACY = ("Der Kandidat nutzt das alte Vokabular ({what}); angelegt werden nur Zeit-Auslöser und das allgemeine "
          "Vokabular (Modus scan). Bitte mit `analysis run --mode scan` neu rechnen.")


class TranslateError(ValueError):
    pass


@dataclass(frozen=True)
class SignalRef:
    handle: str
    entity_id: str
    kind: str          # entities.kind, zum Beispiel person, sensor, flat
    value_kind: str    # categorical | numeric | timestamp


@dataclass(frozen=True)
class RuleInputs:
    triggers: list[dict]
    conditions: list[dict]
    delay_min: int
    action_kind: str
    action_preset: str
    action_setpoint: float


def _ref(signals: dict[str, SignalRef], handle: Any) -> SignalRef:
    ref = signals.get(handle) if isinstance(handle, str) else None
    if ref is None:
        raise TranslateError(f"Signal {handle!r} ist unbekannt")
    return ref


def _weekday_condition(days: list[int]) -> dict:
    return {"condition": "time", "weekday": [WEEKDAYS[day] for day in sorted(days)]}


def _offset_text(minutes: int) -> str:
    sign = "-" if minutes < 0 else ""
    hours, rest = divmod(abs(minutes), 60)
    return f"{sign}{hours:02d}:{rest:02d}:00"


def _state_trigger(ref: SignalRef, value: str) -> dict:
    if ref.kind == "person" and value == "away":
        return {"trigger": "state", "entity_id": ref.entity_id, "from": "home", "not_to": list(UNUSABLE)}
    return {"trigger": "state", "entity_id": ref.entity_id, "to": value, "not_from": list(UNUSABLE)}


def _state_condition(ref: SignalRef, value: str, op: str) -> dict:
    if ref.kind == "person" and value == "away":
        home = {"condition": "state", "entity_id": ref.entity_id, "state": "home"}
        return {"condition": "not", "conditions": [home]} if op == "is" else home
    base = {"condition": "state", "entity_id": ref.entity_id, "state": value}
    return base if op == "is" else {"condition": "not", "conditions": [base]}


def _trigger(raw: dict, signals: dict[str, SignalRef]) -> tuple[list[dict], list[dict], int]:
    kind = raw.get("type")
    if kind == "time":
        days = sorted(raw["weekdays"])
        extra = [] if days == list(range(7)) else [_weekday_condition(days)]
        return [{"trigger": "time", "at": f"{raw['time']}:00"}], extra, 0
    if kind == "event":
        raise TranslateError(LEGACY.format(what=f"Ereignis {raw.get('event')}"))
    if kind != "signal":
        raise TranslateError(f"Unbekannter Auslöser {kind!r}")
    ref = _ref(signals, raw.get("signal"))
    event = raw.get("event")
    offset = int(raw.get("offset_min", 0))
    if event == "alarm_time":
        trigger = {"trigger": "time", "at": ref.entity_id}
        if offset:
            trigger["offset"] = _offset_text(offset)
        return [trigger], [], 0
    if offset < 0:
        raise TranslateError("Ein negativer Versatz ist nur bei alarm_time möglich")
    if event == "to_state":
        extra: list[dict] = []
        low, high = raw.get("min_prev_min"), raw.get("max_prev_min")
        if low is not None or high is not None:
            duration = "((trigger.to_state.last_changed - trigger.from_state.last_changed).total_seconds() / 60)"
            parts = ["trigger.from_state is not none"]
            if low is not None:
                parts.append(f"{duration} >= {int(low)}")
            if high is not None:
                parts.append(f"{duration} < {int(high)}")
            extra.append({"condition": "template", "value_template": "{{ " + " and ".join(parts) + " }}"})
        return [_state_trigger(ref, raw["value"])], extra, offset
    if event in ("rises_above", "falls_below"):
        key = "above" if event == "rises_above" else "below"
        return [{"trigger": "numeric_state", "entity_id": ref.entity_id, key: float(raw["value"])}], [], offset
    raise TranslateError(f"Unbekanntes Signal-Ereignis {event!r}")


def _condition(raw: dict, signals: dict[str, SignalRef]) -> dict:
    kind = raw.get("type")
    if kind == "weekdays":
        return _weekday_condition(raw["weekdays"])
    if kind == "time_window":
        return {"condition": "time", "after": f"{raw['from']}:00", "before": f"{raw['to']}:00"}
    if kind == "signal_state":
        return _state_condition(_ref(signals, raw.get("signal")), raw["value"], raw["op"])
    if kind == "signal_range":
        ref = _ref(signals, raw.get("signal"))
        return {"condition": "numeric_state", "entity_id": ref.entity_id, raw["op"]: float(raw["value"])}
    if kind in ("outside_temp", "person_home"):
        raise TranslateError(LEGACY.format(what=f"Bedingung {kind}"))
    raise TranslateError(f"Unbekannte Bedingung {kind!r}")


def translate(candidate: Candidate, signals: dict[str, SignalRef], *, setpoint_min: float,
              setpoint_max: float) -> RuleInputs:
    triggers, conditions, delay = _trigger(candidate.trigger, signals)
    conditions = list(conditions) + [_condition(item, signals) for item in candidate.conditions]
    if not conditions:
        conditions = [{"condition": "template", "value_template": "{{ true }}"}]
    action = candidate.action
    if action.get("type") == "preset":
        preset = str(action.get("value") or "").strip()
        if not preset:
            raise TranslateError("Das Preset der Aktion ist leer")
        return RuleInputs(triggers, conditions, delay, "preset", preset, 21.0)
    if action.get("type") == "setpoint":
        value = float(action["value"])
        if not setpoint_min <= value <= setpoint_max:
            raise TranslateError(f"Der Sollwert {value:g} °C liegt außerhalb der erlaubten Grenzen "
                                 f"{setpoint_min:g} bis {setpoint_max:g} °C")
        return RuleInputs(triggers, conditions, delay, "setpoint", "", value)
    raise TranslateError(f"Unbekannte Aktion {action.get('type')!r}")


def load_signal_refs(db: Database) -> dict[str, SignalRef]:
    rows = db.conn.execute("SELECT handle, entity_id, kind, value_kind FROM signals ORDER BY handle")
    return {row["handle"]: SignalRef(row["handle"], row["entity_id"], row["kind"], row["value_kind"]) for row in rows}


def climate_for_room(db: Database, area: str) -> str:
    rows = db.conn.execute(
        "SELECT entity_id FROM entities WHERE kind = 'climate' AND active = 1 AND room = ? ORDER BY entity_id",
        (area,)).fetchall()
    if not rows:
        raise TranslateError(f"Für den Raum {area} ist kein Heizkörper aufgezeichnet")
    if len(rows) > 1:
        raise TranslateError(f"Für den Raum {area} gibt es mehrere Heizkörper: "
                             f"{', '.join(row['entity_id'] for row in rows)}")
    return rows[0]["entity_id"]
