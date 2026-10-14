from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta

from collector.models import parse_utc

from .config import ScanParams
from .signals import Signal, SignalLibrary

THROTTLE = timedelta(minutes=60)       # Schwellenüberschreitungen höchstens einmal je Stunde
STATE_THROTTLE = timedelta(minutes=15)  # Zustandswechsel in denselben Zustand höchstens einmal je Viertelstunde (flatternde Melder)
ALL = "alle"


def class_labels(edges: tuple[int, ...]) -> list[str]:
    labels = [f"<{edges[0]}"]
    labels += [f"{low}-{high}" for low, high in zip(edges, edges[1:])]
    labels.append(f"≥{edges[-1]}")
    return labels


def class_of(minutes: float, edges: tuple[int, ...]) -> str:
    return class_labels(edges)[bisect_right(edges, minutes)]


@dataclass(frozen=True)
class EventSpec:
    signal: str
    event: str                       # to_state | rises_above | falls_below | alarm_time
    value: str | None                # Zustand oder Schwelle als Text
    prev_class: str                  # "alle" oder Dauerklasse des vorherigen Zustands


@dataclass
class EventSeries:
    spec: EventSpec
    times: list[datetime]
    prev: list[float | None]         # Minuten im Zustand davor (nur to_state)


def _to_state(signal: Signal, value: str) -> list[tuple[datetime, float | None]]:
    found = []
    previous_state, started = None, None
    for index, (ts, state) in enumerate(signal.timeline.points()):
        if index == 0:
            previous_state, started = state, ts
            continue
        if state == previous_state:
            continue
        if state == value and previous_state is not None and (not found or ts - found[-1][0] >= STATE_THROTTLE):
            found.append((ts, (ts - started).total_seconds() / 60))
        previous_state, started = state, ts
    return found


def _crossings(signal: Signal, threshold: float, rising: bool) -> list[tuple[datetime, float | None]]:
    found: list[tuple[datetime, float | None]] = []
    previous: float | None = None
    last: datetime | None = None
    for ts, state in signal.timeline.points():
        current = None if state is None else float(state)
        if current is None:
            previous = None
            continue
        if previous is not None:
            crossed = previous < threshold <= current if rising else previous >= threshold > current
            if crossed and (last is None or ts - last >= THROTTLE):
                found.append((ts, None))
                last = ts
        previous = current
    return found


def _alarms(signal: Signal, until: datetime | None) -> list[tuple[datetime, float | None]]:
    """Ein beobachteter Zeitstempel T zählt, wenn der Sensor eine Minute vor T noch auf T stand."""
    found, seen = [], set()
    for ts, state in signal.timeline.points():
        if state is None or state in seen:
            continue
        try:
            moment = parse_utc(state)
        except ValueError:
            continue
        if moment <= ts or (until is not None and moment > until):
            continue
        if signal.timeline.value_at(moment - timedelta(minutes=1)) == state:
            found.append((moment, None))
            seen.add(state)
    return found


def signal_events(signal: Signal, event: str, value=None,
                  until: datetime | None = None) -> list[tuple[datetime, float | None]]:
    """Zeitpunkte (und beim Zustandswechsel die Minuten im Zustand davor) eines Ereignisses."""
    if event == "to_state":
        found = _to_state(signal, str(value))
    elif event == "rises_above":
        found = _crossings(signal, float(value), True)
    elif event == "falls_below":
        found = _crossings(signal, float(value), False)
    elif event == "alarm_time":
        found = _alarms(signal, until)
    else:
        raise ValueError(f"unbekanntes Ereignis {event!r}")
    return sorted(found, key=lambda item: item[0])


def event_series(library: SignalLibrary, params: ScanParams, until: datetime | None = None) -> list[EventSeries]:
    """Alle Ereignisarten, die es in den Daten genügend oft gibt."""
    series: list[EventSeries] = []

    def add(signal: Signal, event: str, value: str | None, prev_class: str, items: list) -> None:
        if len(items) >= params.min_events:
            series.append(EventSeries(EventSpec(signal.handle, event, value, prev_class),
                                      [ts for ts, _ in items], [prev for _, prev in items]))

    for signal in library.usable():
        kind = signal.info.value_kind
        if kind == "categorical":
            for value in signal.info.values:
                items = signal_events(signal, "to_state", value)
                add(signal, "to_state", value, ALL, items)
                for label in class_labels(params.duration_edges):
                    add(signal, "to_state", value, label,
                        [(ts, prev) for ts, prev in items if class_of(prev, params.duration_edges) == label])
        elif kind == "numeric":
            for threshold in sorted(set(signal.info.terciles)):
                for event in ("rises_above", "falls_below"):
                    add(signal, event, f"{threshold:g}", ALL, signal_events(signal, event, threshold))
        elif kind == "timestamp":
            add(signal, "alarm_time", None, ALL, signal_events(signal, "alarm_time", until=until))
    return series


def event_text(spec: EventSpec) -> str:
    if spec.event == "to_state":
        text = f"wechselt zu {spec.value}"
        return text if spec.prev_class == ALL else f"{text} (vorher {spec.prev_class} min im anderen Zustand)"
    if spec.event == "rises_above":
        return f"steigt über {spec.value}"
    if spec.event == "falls_below":
        return f"fällt unter {spec.value}"
    return "Weckzeit erreicht"
