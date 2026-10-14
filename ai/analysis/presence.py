"""Zusammenhang zwischen Anwesenheit und manuellen Eingriffen (rein lokal, ohne LLM)."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from collector.db import Database

from .config import AnalysisConfig
from .context import UNKNOWN
from .interventions import load_interventions
from .pseudonym import build_pseudonyms
from .timeline import load_timeline

BIN_MINUTES = 30
BIN_STARTS = tuple(range(-120, 120, BIN_MINUTES))        # zwei Stunden davor bis zwei Stunden danach
SPAN_MINUTES = 120
BUCKETS = (("≤15 min", 15), ("≤30 min", 30), ("≤60 min", 60), ("≤120 min", 120), ("≤240 min", 240))
LATER = "später/nie"
CONTROL_DAYS = 7


@dataclass
class EventRow:
    person: str
    event: str                       # kommt | geht
    raw_count: int                   # alle Wechsel an Tagen mit Eingriffen
    count: int                       # davon nach dem Filter (Zustand vorher mindestens min_state_min lang)
    hits: dict[int, int]             # Abschnittsbeginn (Minuten) -> Ereignisse mit Eingriff im Abschnitt
    control_count: int
    control_hits: dict[int, int]


@dataclass
class PresenceReport:
    min_state_min: int = 120
    interventions: int = 0
    days: int = 0
    rows: list[EventRow] = field(default_factory=list)
    delays: dict[str, tuple[int, int]] = field(default_factory=dict)
    grid_total: int = 0


def _count(times: list[datetime], low: datetime, high: datetime) -> int:
    return bisect_left(times, high) - bisect_left(times, low)


def _bucket(minutes: float | None) -> str:
    if minutes is not None:
        for label, limit in BUCKETS:
            if minutes <= limit:
                return label
    return LATER


def _events(transitions: list, kind: str, min_state_min: int, first: datetime | None) -> list[tuple[datetime, bool]]:
    """(Zeitpunkt, bestanden) für Ankünfte (`kommt`) oder Abgänge (`geht`).

    Ein Ereignis besteht den Filter, wenn der Zustand davor mindestens `min_state_min` Minuten lang galt.
    Das schließt das Springen zwischen home und not_home aus. Der erste bekannte Zustand gilt ab dem ersten Messpunkt.
    """
    found = []
    for index, (ts, old, new) in enumerate(transitions):
        if kind == "kommt":
            wanted = new == "home" and old not in UNKNOWN
        else:
            wanted = old == "home" and new not in UNKNOWN
        if not wanted:
            continue
        previous = transitions[index - 1][0] if index > 0 else first
        long_enough = previous is not None and (ts - previous).total_seconds() / 60 >= min_state_min
        found.append((ts, long_enough))
    return found


def analyze(db: Database, cfg: AnalysisConfig, min_state_min: int = 120) -> PresenceReport:
    report = PresenceReport(min_state_min=min_state_min)
    interventions = load_interventions(db, cfg)
    if not interventions:
        return report
    tz = ZoneInfo(cfg.timezone)
    times = sorted(item.ts for item in interventions)
    report.interventions = len(times)
    intervention_days = {ts.astimezone(tz).date() for ts in times}
    active_days = {day + timedelta(days=delta) for day in intervention_days for delta in (-1, 0, 1)}
    report.days = len(intervention_days)

    def shifted(ts: datetime, days: int) -> datetime:
        local = ts.astimezone(tz)
        return datetime.combine(local.date() + timedelta(days=days), local.time(), tz).astimezone(timezone.utc)

    pseudo = build_pseudonyms(db)
    all_arrivals: list[datetime] = []
    for entity_id in pseudo.persons:
        timeline = load_timeline(db, entity_id)
        if not timeline:
            continue
        transitions = timeline.transitions()
        name = pseudo.display_name(entity_id)
        for kind in ("kommt", "geht"):
            events = _events(transitions, kind, min_state_min, timeline.first_ts())
            in_scope = [(ts, ok) for ts, ok in events if ts.astimezone(tz).date() in active_days]
            used = sorted(ts for ts, ok in in_scope if ok)
            if kind == "kommt":
                all_arrivals.extend(ts for ts, ok in events if ok)
            if not in_scope:
                continue
            hits = {start: 0 for start in BIN_STARTS}
            control_hits = {start: 0 for start in BIN_STARTS}
            control_count = 0
            for ts in used:
                for start in BIN_STARTS:
                    low = ts + timedelta(minutes=start)
                    hits[start] += _count(times, low, low + timedelta(minutes=BIN_MINUTES)) > 0
                for delta in range(-CONTROL_DAYS, CONTROL_DAYS + 1):
                    if delta == 0:
                        continue
                    other = shifted(ts, delta)
                    if other.astimezone(tz).date() not in active_days:
                        continue
                    if _count(used, other - timedelta(minutes=SPAN_MINUTES), other + timedelta(minutes=SPAN_MINUTES)):
                        continue
                    control_count += 1
                    for start in BIN_STARTS:
                        low = other + timedelta(minutes=start)
                        control_hits[start] += _count(times, low, low + timedelta(minutes=BIN_MINUTES)) > 0
            report.rows.append(EventRow(name, kind, len(in_scope), len(used), hits, control_count, control_hits))

    all_arrivals.sort()
    grid = [datetime.combine(day, time(hour, minute), tz).astimezone(timezone.utc)
            for day in sorted(intervention_days) for hour in range(6, 24) for minute in (0, 30)]
    report.grid_total = len(grid)

    def since_last_arrival(ts: datetime) -> float | None:
        index = bisect_right(all_arrivals, ts) - 1
        return None if index < 0 else (ts - all_arrivals[index]).total_seconds() / 60

    counts = {label: [0, 0] for label, _ in BUCKETS} | {LATER: [0, 0]}
    for ts in times:
        counts[_bucket(since_last_arrival(ts))][0] += 1
    for ts in grid:
        counts[_bucket(since_last_arrival(ts))][1] += 1
    report.delays = {label: (pair[0], pair[1]) for label, pair in counts.items()}
    return report


def _pct(part: int, total: int) -> str:
    return "-" if not total else f"{100 * part / total:.0f}%"


def render(report: PresenceReport) -> str:
    if not report.interventions:
        return "Keine manuellen Eingriffe gefunden."
    lines = [f"{report.interventions} manuelle Eingriffe an {report.days} Tagen. Ausgewertet werden nur Tage mit "
             f"Eingriffen (±1 Tag), Zeiten in lokaler Zeit.", "",
             "Ankünfte und Abgänge. Je Zeile ein 30-Minuten-Abschnitt relativ zum Ereignis (negativ = davor).",
             "Zahl: Anteil der Ereignisse mit einem Eingriff (irgendein Raum) in diesem Abschnitt.",
             "In Klammern die Kontrolle: dieselbe Uhrzeit an anderen Tagen ohne dieses Ereignis.",
             f"Gezählt werden nur Ereignisse, vor denen der Zustand mindestens {report.min_state_min} min galt "
             "(filtert das Springen zwischen zu Hause und unterwegs):"]
    for item in report.rows:
        lines.append(f"  {item.person} {item.event}: {item.count} von {item.raw_count} Wechseln")
    names = [f"{item.person} {item.event}" for item in report.rows]
    lines.append(f"{'Minuten':<15}" + "".join(f"{name:>18}" for name in names))
    for start in BIN_STARTS:
        cells = "".join(
            f"{_pct(item.hits[start], item.count) + ' (' + _pct(item.control_hits[start], item.control_count) + ')':>18}"
            for item in report.rows)
        lines.append(f"{f'{start} bis {start + BIN_MINUTES}':<15}{cells}")
    lines += ["", "Zeit seit der letzten Ankunft (irgendeiner Person): Eingriffe verglichen mit zufälligen Zeitpunkten.",
              f"{'':<12}{'Eingriffe':>16}{'Zufall':>16}"]
    for label, (count, grid_count) in report.delays.items():
        lines.append(f"{label:<12}{_pct(count, report.interventions):>9} ({count:>3}){_pct(grid_count, report.grid_total):>9} ({grid_count:>4})")
    return "\n".join(lines)
