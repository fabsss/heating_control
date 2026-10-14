from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import exp, lgamma
from zoneinfo import ZoneInfo

from .config import AnalysisConfig
from .events import EventSeries, EventSpec, event_series, event_text
from .features import room_name
from .models import Association, Intervention
from .signals import SignalLibrary

BIN_MINUTES = 30
SPAN_MINUTES = 120
BIN_STARTS = tuple(range(-SPAN_MINUTES, SPAN_MINUTES, BIN_MINUTES))     # zwei Stunden davor bis danach


def fisher_greater(hits: int, events: int, control_hits: int, control_events: int) -> float:
    """Einseitiger exakter Test: Wie wahrscheinlich sind mindestens so viele Treffer wie beobachtet,
    wenn Ereignisse und Kontrolle denselben Trefferanteil hätten (hypergeometrische Verteilung)?"""
    if hits <= 0 or events <= 0 or control_events <= 0:
        return 1.0
    total = events + control_events
    successes = hits + control_hits
    log_p = (lgamma(successes + 1) - lgamma(hits + 1) - lgamma(successes - hits + 1)
             + lgamma(total - successes + 1) - lgamma(events - hits + 1)
             - lgamma(total - successes - events + hits + 1)
             - (lgamma(total + 1) - lgamma(events + 1) - lgamma(total - events + 1)))
    pmf = exp(log_p)
    tail = pmf
    for k in range(hits, min(events, successes)):
        pmf *= ((successes - k) * (events - k)) / ((k + 1) * (total - successes - events + k + 1))
        tail += pmf
    return min(1.0, tail)


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Korrigierte Werte (q) für viele gleichzeitige Tests; steuert den Anteil falscher Funde."""
    count = len(p_values)
    order = sorted(range(count), key=lambda index: p_values[index])
    q_values = [1.0] * count
    running = 1.0
    for rank in range(count, 0, -1):
        index = order[rank - 1]
        running = min(running, p_values[index] * count / rank)
        q_values[index] = running
    return q_values


def _hit_bins(times: list[datetime], instant: datetime) -> set[int]:
    low = bisect_left(times, instant - timedelta(minutes=SPAN_MINUTES))
    high = bisect_left(times, instant + timedelta(minutes=SPAN_MINUTES))
    bins = set()
    for ts in times[low:high]:
        minutes = (ts - instant).total_seconds() / 60
        bins.add(int(minutes // BIN_MINUTES) * BIN_MINUTES)
    return bins


def _first_in_window(times: list[datetime], instant: datetime, low: int, high: int) -> int | None:
    index = bisect_left(times, instant + timedelta(minutes=low))
    if index < len(times) and times[index] < instant + timedelta(minutes=high):
        return index
    return None


@dataclass
class _Test:
    series: int
    room: str | None
    start: int
    hits: int
    events: int
    control_hits: int
    control_events: int
    p: float
    q: float = 1.0


def _lift(hits: int, events: int, control_hits: int, control_events: int) -> float:
    control_rate = max(control_hits / control_events, 1 / (control_events + 1))
    return (hits / events) / control_rate


def _action_text(items: list[Intervention], hits: int) -> str:
    if not items:
        return ""
    counts = Counter((item.kind, round(float(item.value) * 2) / 2 if item.kind == "setpoint" else item.value)
                     for item in items)
    (kind, value), count = counts.most_common(1)[0]
    what = f"Sollwert {value:g} °C" if kind == "setpoint" else f"Preset {value}"
    return f"{what} ({count} von {hits})"


def run_scan(cfg: AnalysisConfig, library: SignalLibrary, interventions: list[Intervention],
             start: datetime, end: datetime) -> list[Association]:
    """Prüft jede Ereignisart gegen die Eingriffe je Raum; Ergebnis nach q und lift geordnet."""
    params = cfg.scan
    if not interventions:
        return []
    tz = ZoneInfo(cfg.timezone)
    ordered = sorted(interventions, key=lambda item: item.ts)
    room_items: dict[str | None, list[Intervention]] = {None: ordered}
    for room, count in Counter(item.room for item in ordered).items():
        if count >= params.min_room_interventions:
            room_items[room] = [item for item in ordered if item.room == room]
    room_times = {room: [item.ts for item in items] for room, items in room_items.items()}
    days = {item.ts.astimezone(tz).date() for item in ordered}
    active = {day + timedelta(days=delta) for day in days for delta in (-1, 0, 1)}

    def shifted(ts: datetime, days_: int) -> datetime:
        local = ts.astimezone(tz)
        return datetime.combine(local.date() + timedelta(days=days_), local.time(), tz).astimezone(timezone.utc)

    all_series: list[EventSeries] = event_series(library, params, until=end)
    kept: dict[int, tuple[list[datetime], list[datetime]]] = {}
    tests: list[_Test] = []
    for index, series in enumerate(all_series):
        events = [ts for ts in series.times if start <= ts < end and ts.astimezone(tz).date() in active]
        if len(events) < params.min_events:
            continue
        controls = []
        for ts in events:
            for delta in range(-params.control_days, params.control_days + 1):
                other = shifted(ts, delta)
                if delta == 0 or other.astimezone(tz).date() not in active:
                    continue
                low = bisect_left(events, other - timedelta(minutes=SPAN_MINUTES))
                if bisect_left(events, other + timedelta(minutes=SPAN_MINUTES)) > low:
                    continue
                controls.append(other)
        if not controls:
            continue
        kept[index] = (events, controls)
        for room, times in room_times.items():
            hits: Counter[int] = Counter()
            control_hits: Counter[int] = Counter()
            for ts in events:
                hits.update(_hit_bins(times, ts))
            for ts in controls:
                control_hits.update(_hit_bins(times, ts))
            for bin_start in BIN_STARTS:
                p = 1.0
                if hits[bin_start] / len(events) > control_hits[bin_start] / len(controls):
                    p = fisher_greater(hits[bin_start], len(events), control_hits[bin_start], len(controls))
                tests.append(_Test(index, room, bin_start, hits[bin_start], len(events),
                                   control_hits[bin_start], len(controls), p))
    for test, q in zip(tests, benjamini_hochberg([test.p for test in tests])):
        test.q = q

    passing: dict[tuple[int, str | None], list[_Test]] = {}
    for test in tests:
        if test.hits >= params.min_hits and test.q <= params.q_max and \
                _lift(test.hits, test.events, test.control_hits, test.control_events) > 1:
            passing.setdefault((test.series, test.room), []).append(test)

    found: list[Association] = []
    for (index, room), group in passing.items():
        group.sort(key=lambda test: test.start)
        runs: list[list[_Test]] = [[group[0]]]
        for test in group[1:]:
            if test.start == runs[-1][-1].start + BIN_MINUTES:
                runs[-1].append(test)
            else:
                runs.append([test])
        events, controls = kept[index]
        times, items = room_times[room], room_items[room]
        spec = all_series[index].spec
        for run in runs:
            low, high = run[0].start, run[-1].start + BIN_MINUTES
            hit_items = []
            for ts in events:
                position = _first_in_window(times, ts, low, high)
                if position is not None:
                    hit_items.append(items[position])
            control_hits = sum(_first_in_window(times, ts, low, high) is not None for ts in controls)
            best = min(run, key=lambda test: (test.q, test.p))
            found.append(Association(
                spec.signal, spec.event, spec.value, spec.prev_class, room, low, high, len(events),
                len(hit_items), len(controls), control_hits,
                _lift(len(hit_items), len(events), control_hits, len(controls)), best.p, best.q,
                _action_text(hit_items, len(hit_items))))
    found.sort(key=lambda item: (item.q, -item.lift, -item.hits))
    return found


def association_row(item: Association, cfg: AnalysisConfig) -> dict:
    """Eine Assoziation als Zeile für das Modell (nur Kennungen und Klartext der Räume)."""
    return {
        "signal": item.signal,
        "ereignis": event_text(EventSpec(item.signal, item.event, item.value, item.prev_class)),
        "raum": "alle Räume" if item.room is None else room_name(cfg, item.room),
        "fenster_min": [item.win_start, item.win_end],
        "ereignisse": item.events,
        "mit_eingriff": item.hits,
        "kontrolle_anteil": round(item.control_hits / item.control_events, 2),
        "lift": round(item.lift, 1),
        "q": float(f"{item.q:.2g}"),
        "typische_aktion": item.typical_action,
    }
