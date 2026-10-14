"""Zusammenhang zwischen Anwesenheit und manuellen Eingriffen (rein lokal, ohne LLM)."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from collector.db import Database

from .config import AnalysisConfig
from .context import UNKNOWN
from .interventions import load_interventions
from .pseudonym import build_pseudonyms
from .timeline import Timeline, load_timeline

BUCKETS = (("≤15 min", 15), ("≤30 min", 30), ("≤60 min", 60), ("≤120 min", 120), ("≤240 min", 240))
LATER = "später/nie"
CONTROL_DAYS = 7


@dataclass
class ArrivalRow:
    person: str
    event: str                       # kommt | geht
    count: int
    hits: dict[int, int]
    control_count: int
    control_hits: dict[int, int]


@dataclass
class PresenceRow:
    person: str
    interventions: int
    at_interventions: int
    baseline_total: int
    baseline_home: int


@dataclass
class PresenceReport:
    windows: tuple[int, ...]
    interventions: int = 0
    days: int = 0
    arrivals: list[ArrivalRow] = field(default_factory=list)
    presence: list[PresenceRow] = field(default_factory=list)
    delays: dict[str, tuple[int, int]] = field(default_factory=dict)
    grid_total: int = 0


def _any_between(times: list[datetime], low: datetime, high: datetime) -> bool:
    return bisect_left(times, low) < bisect_right(times, high)


def _bucket(minutes: float | None) -> str:
    if minutes is not None:
        for label, limit in BUCKETS:
            if minutes <= limit:
                return label
    return LATER


def analyze(db: Database, cfg: AnalysisConfig, windows: tuple[int, ...] = (15, 30, 60, 120)) -> PresenceReport:
    report = PresenceReport(windows=windows)
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
    timelines: dict[str, Timeline] = {}
    arrivals_by_person: dict[str, list[datetime]] = {}
    leaves_by_person: dict[str, list[datetime]] = {}
    for entity_id in pseudo.persons:
        timeline = load_timeline(db, entity_id)
        if not timeline:
            continue
        timelines[entity_id] = timeline
        arrivals_by_person[entity_id] = [ts for ts, old, new in timeline.transitions()
                                         if new == "home" and old not in UNKNOWN]
        leaves_by_person[entity_id] = [ts for ts, old, new in timeline.transitions()
                                       if old == "home" and new not in UNKNOWN]

    longest = max(windows)
    for entity_id, timeline in timelines.items():
        name = pseudo.display_name(entity_id)
        for label, events in (("kommt", arrivals_by_person[entity_id]), ("geht", leaves_by_person[entity_id])):
            events = sorted(events)
            used = [ts for ts in events if ts.astimezone(tz).date() in active_days]
            if not used:
                continue
            hits = {w: sum(_any_between(times, ts, ts + timedelta(minutes=w)) for ts in used) for w in windows}
            control_count = 0
            control_hits = {w: 0 for w in windows}
            for ts in used:
                for delta in range(-CONTROL_DAYS, CONTROL_DAYS + 1):
                    if delta == 0:
                        continue
                    other = shifted(ts, delta)
                    if other.astimezone(tz).date() not in active_days:
                        continue
                    if _any_between(events, other - timedelta(minutes=15), other + timedelta(minutes=longest)):
                        continue
                    control_count += 1
                    for w in windows:
                        control_hits[w] += _any_between(times, other, other + timedelta(minutes=w))
            report.arrivals.append(ArrivalRow(name, label, len(used), hits, control_count, control_hits))

    grid = [datetime.combine(day, time(hour, minute), tz).astimezone(timezone.utc)
            for day in sorted(intervention_days) for hour in range(6, 24) for minute in (0, 30)]
    report.grid_total = len(grid)
    for entity_id, timeline in timelines.items():
        known = [ts for ts in times if timeline.value_at(ts) not in UNKNOWN]
        base = [ts for ts in grid if timeline.value_at(ts) not in UNKNOWN]
        report.presence.append(PresenceRow(
            pseudo.display_name(entity_id), len(known),
            sum(timeline.value_at(ts) == "home" for ts in known), len(base),
            sum(timeline.value_at(ts) == "home" for ts in base)))

    all_arrivals = sorted(ts for events in arrivals_by_person.values() for ts in events)

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
    return "  -  " if not total else f"{100 * part / total:.0f}%"


def render(report: PresenceReport) -> str:
    if not report.interventions:
        return "Keine manuellen Eingriffe gefunden."
    lines = [f"{report.interventions} manuelle Eingriffe an {report.days} Tagen. Ausgewertet werden nur Tage mit "
             f"Eingriffen (±1 Tag), Zeiten in lokaler Zeit.", ""]
    lines.append("Ankünfte und Abgänge: Anteil, auf den ein Eingriff in irgendeinem Raum folgt.")
    lines.append("In Klammern die Kontrolle: dieselbe Uhrzeit an anderen Tagen ohne dieses Ereignis.")
    header = f"{'Person':<12}{'Ereignis':<8}{'Anzahl':>7}  " + "  ".join(f"{'in ' + str(w) + ' min':>16}" for w in report.windows)
    lines.append(header)
    for row in report.arrivals:
        cells = "  ".join(f"{_pct(row.hits[w], row.count):>7} ({_pct(row.control_hits[w], row.control_count):>5})"
                          .rjust(16) for w in report.windows)
        lines.append(f"{row.person:<12}{row.event:<8}{row.count:>7}  {cells}")
    lines += ["", "Anwesenheit bei Eingriffen: Anteil, bei dem die Person zu Hause war,",
              "verglichen mit dem Anteil der Zeit (06 bis 24 Uhr, an Tagen mit Eingriffen), in der sie zu Hause ist."]
    for row in report.presence:
        lines.append(f"{row.person:<12}bei Eingriffen {_pct(row.at_interventions, row.interventions):>5} "
                     f"({row.at_interventions}/{row.interventions})   im Mittel {_pct(row.baseline_home, row.baseline_total):>5}")
    lines += ["", "Zeit seit der letzten Ankunft (irgendeiner Person): Eingriffe verglichen mit zufälligen Zeitpunkten.",
              f"{'':<12}{'Eingriffe':>16}{'Zufall':>16}"]
    total_grid = report.grid_total
    for label, (count, grid_count) in report.delays.items():
        lines.append(f"{label:<12}{_pct(count, report.interventions):>9} ({count:>3}){_pct(grid_count, total_grid):>9} ({grid_count:>4})")
    return "\n".join(lines)
