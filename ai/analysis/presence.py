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
DEFAULT_EDGES = (45, 180)          # Minuten: unter 45 = Gassi, bis 180 = Einkauf, darüber = Arbeit


@dataclass
class EventRow:
    person: str
    klass: str                       # Abwesenheitsdauer vor der Ankunft: <45 | 45-180 | ≥180
    count: int
    hits: dict[int, int]             # Abschnittsbeginn (Minuten) -> Ankünfte mit Eingriff im Abschnitt
    control_count: int
    control_hits: dict[int, int]


@dataclass
class PresenceReport:
    edges: tuple[int, ...] = DEFAULT_EDGES
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


def class_labels(edges: tuple[int, ...]) -> list[str]:
    labels = [f"<{edges[0]}"]
    labels += [f"{low}-{high}" for low, high in zip(edges, edges[1:])]
    labels.append(f"≥{edges[-1]}")
    return labels


def _arrivals(transitions: list, first: datetime | None) -> list[tuple[datetime, float]]:
    """(Zeitpunkt, Minuten der Abwesenheit davor) für jeden Wechsel auf `home` aus einem bekannten Zustand.

    Die Abwesenheit läuft vom vorigen Wechsel bis zur Ankunft, beim ersten bekannten Zustand ab dem ersten Messpunkt.
    """
    found = []
    for index, (ts, old, new) in enumerate(transitions):
        if new != "home" or old in UNKNOWN:
            continue
        previous = transitions[index - 1][0] if index > 0 else first
        if previous is not None:
            found.append((ts, (ts - previous).total_seconds() / 60))
    return found


def analyze(db: Database, cfg: AnalysisConfig, edges: tuple[int, ...] = DEFAULT_EDGES) -> PresenceReport:
    edges = tuple(sorted(edges))
    report = PresenceReport(edges=edges)
    interventions = load_interventions(db, cfg)
    if not interventions:
        return report
    tz = ZoneInfo(cfg.timezone)
    times = sorted(item.ts for item in interventions)
    report.interventions = len(times)
    intervention_days = {ts.astimezone(tz).date() for ts in times}
    active_days = {day + timedelta(days=delta) for day in intervention_days for delta in (-1, 0, 1)}
    report.days = len(intervention_days)
    labels = class_labels(edges)

    def shifted(ts: datetime, days: int) -> datetime:
        local = ts.astimezone(tz)
        return datetime.combine(local.date() + timedelta(days=days), local.time(), tz).astimezone(timezone.utc)

    pseudo = build_pseudonyms(db)
    all_arrivals: list[datetime] = []
    for entity_id in pseudo.persons:
        timeline = load_timeline(db, entity_id)
        if not timeline:
            continue
        name = pseudo.display_name(entity_id)
        arrivals = _arrivals(timeline.transitions(), timeline.first_ts())
        all_arrivals.extend(ts for ts, away in arrivals if away >= edges[0])
        scoped = [(ts, away) for ts, away in arrivals if ts.astimezone(tz).date() in active_days]
        every = sorted(ts for ts, _ in scoped)
        for index, label in enumerate(labels):
            used = sorted(ts for ts, away in scoped if bisect_right(edges, away) == index)
            if not used:
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
                    if _count(every, other - timedelta(minutes=SPAN_MINUTES), other + timedelta(minutes=SPAN_MINUTES)):
                        continue
                    control_count += 1
                    for start in BIN_STARTS:
                        low = other + timedelta(minutes=start)
                        control_hits[start] += _count(times, low, low + timedelta(minutes=BIN_MINUTES)) > 0
            report.rows.append(EventRow(name, label, len(used), hits, control_count, control_hits))

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
    labels = class_labels(report.edges)
    lines = [f"{report.interventions} manuelle Eingriffe an {report.days} Tagen. Ausgewertet werden nur Tage mit "
             f"Eingriffen (±1 Tag), Zeiten in lokaler Zeit.", "",
             "Ankünfte nach Dauer der Abwesenheit davor (Minuten; kurz = Gassi, mittel = Einkauf, lang = Arbeit).",
             "Je Zeile ein 30-Minuten-Abschnitt relativ zur Ankunft (negativ = davor).",
             "Zahl: Anteil der Ankünfte mit einem Eingriff (irgendein Raum) in diesem Abschnitt.",
             "In Klammern die Kontrolle: dieselbe Uhrzeit an anderen Tagen ohne Ankunft derselben Person.",
             "Anzahl der Ankünfte:"]
    for item in report.rows:
        lines.append(f"  {item.person} {item.klass}: {item.count}")
    names = [f"{item.person} {item.klass}" for item in report.rows]
    lines.append(f"{'Minuten':<13}" + "".join(f"{name:>16}" for name in names))
    for start in BIN_STARTS:
        cells = "".join(
            f"{_pct(item.hits[start], item.count) + ' (' + _pct(item.control_hits[start], item.control_count) + ')':>16}"
            for item in report.rows)
        lines.append(f"{f'{start} bis {start + BIN_MINUTES}':<13}{cells}")
    lines += ["", f"Zeit seit der letzten Ankunft nach mindestens {report.edges[0]} min Abwesenheit "
                  "(irgendeine Person): Eingriffe verglichen mit zufälligen Zeitpunkten.",
              f"{'':<12}{'Eingriffe':>16}{'Zufall':>16}"]
    for label, (count, grid_count) in report.delays.items():
        lines.append(f"{label:<12}{_pct(count, report.interventions):>9} ({count:>3}){_pct(grid_count, report.grid_total):>9} ({grid_count:>4})")
    return "\n".join(lines)
