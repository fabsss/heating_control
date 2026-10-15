from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import AnalysisConfig
from .context import UNKNOWN, ContextProvider
from .events import signal_events
from .interventions import excluded
from .models import (STATUS_FEW, STATUS_PASSED, STATUS_REJECTED, STATUS_UNTESTABLE, Candidate,
                     Intervention)


class NotTestable(Exception):
    pass


@dataclass
class BacktestResult:
    status: str
    note: str
    metrics: dict[str, float]


class Backtester:
    """Rechnet Kandidaten gegen die Historie nach. Kein LLM beteiligt."""

    def __init__(self, cfg: AnalysisConfig, interventions: list[Intervention], ctx: ContextProvider,
                 start: datetime, end: datetime):
        self.cfg = cfg
        self.ctx = ctx
        self.start = start
        self.end = end
        self.tz = ZoneInfo(cfg.timezone)
        self._pool: dict[tuple[str, str], list[Intervention]] = defaultdict(list)
        for item in sorted(interventions, key=lambda entry: entry.ts):
            self._pool[(item.room, item.kind)].append(item)
        self._active = self._active_periods(interventions)

    def _active_periods(self, interventions: list[Intervention]) -> dict[str, list[tuple[datetime, datetime]]]:
        """Zeiträume je Raum, in denen überhaupt eingegriffen wird; große Pausen (Sommer) zählen nicht."""
        gap = timedelta(days=self.cfg.backtest.active_gap_days)
        if gap <= timedelta(0):
            return {}
        by_room: dict[str, list[datetime]] = defaultdict(list)
        for item in interventions:
            by_room[item.room].append(item.ts)
        periods: dict[str, list[tuple[datetime, datetime]]] = {}
        for room, times in by_room.items():
            times.sort()
            spans = []
            first = last = times[0]
            for ts in times[1:]:
                if ts - last > gap:
                    spans.append(self._span(first, last))
                    first = ts
                last = ts
            spans.append(self._span(first, last))
            periods[room] = spans
        return periods

    def _span(self, first: datetime, last: datetime) -> tuple[datetime, datetime]:
        """Ganze lokale Tage: ein Tag vor dem ersten bis ein Tag nach dem letzten Eingriff."""
        def midnight(ts: datetime, days: int) -> datetime:
            local = ts.astimezone(self.tz).date() + timedelta(days=days)
            return datetime(local.year, local.month, local.day, tzinfo=self.tz).astimezone(timezone.utc)
        return midnight(first, -1), midnight(last, 2)

    def _is_active(self, room: str, instant: datetime) -> bool:
        spans = self._active.get(room)
        return True if not spans else any(start <= instant < end for start, end in spans)

    def _is_neutral(self, candidate: Candidate, instant: datetime) -> bool:
        """Die Regel hätte nichts verändert: Heizung aus oder der Zielwert gilt bereits."""
        informative = [s for s in self.ctx.climate(candidate.room) if s.state.value_at(instant) is not None]
        if not informative:
            return False
        running = [s for s in informative if s.state.value_at(instant) not in ("off", "unavailable", "unknown")]
        if not running:
            return True
        action = candidate.action
        for item in running:
            if action["type"] == "preset":
                if item.preset.value_at(instant) != str(action["value"]):
                    return False
            else:
                current = item.temperature.numeric_at(instant)
                if current is None or abs(current - float(action["value"])) > self.cfg.backtest.setpoint_tolerance:
                    return False
        return True

    def evaluate(self, candidate: Candidate) -> BacktestResult:
        try:
            instants = self._trigger_instants(candidate)
        except NotTestable as exc:
            return BacktestResult(STATUS_UNTESTABLE, str(exc), {})
        try:
            self._check_condition_data(candidate)
        except NotTestable as exc:
            return BacktestResult(STATUS_UNTESTABLE, str(exc), {})
        instants = [instant for instant in instants
                    if self.start <= instant < self.end
                    and not excluded(self.cfg, candidate.room, instant)
                    and self._is_active(candidate.room, instant)
                    and self._conditions_hold(candidate, instant)]
        return self._measure(candidate, instants)

    # --- Auslöser ---------------------------------------------------------------

    def _trigger_instants(self, candidate: Candidate) -> list[datetime]:
        trigger = candidate.trigger
        if trigger["type"] == "time":
            return self._time_instants(trigger)
        if trigger["type"] == "signal":
            return self._signal_instants(trigger)
        return self._event_instants(trigger, candidate.room)

    def _signal(self, handle: str):
        library = getattr(self.ctx, "signals", None)
        signal = library.get(handle) if library is not None else None
        if signal is None or not signal.timeline:
            raise NotTestable("Signal ohne aufgezeichnete Daten")
        return signal

    def _signal_instants(self, trigger: dict) -> list[datetime]:
        signal = self._signal(trigger["signal"])
        events = signal_events(signal, trigger["event"], trigger.get("value"), until=self.end)
        if not events:
            raise NotTestable("keine Ereignisse für das Signal")
        low, high = trigger.get("min_prev_min"), trigger.get("max_prev_min")
        offset = timedelta(minutes=trigger.get("offset_min", 0))
        result = []
        for ts, previous in events:
            if previous is not None and ((low is not None and previous < low) or (high is not None and previous >= high)):
                continue
            result.append(ts + offset)
        return result

    def _time_instants(self, trigger: dict) -> list[datetime]:
        hour, minute = (int(part) for part in trigger["time"].split(":"))
        current = self.start.astimezone(self.tz).date()
        last = self.end.astimezone(self.tz).date()
        instants = []
        while current <= last:
            if current.weekday() in trigger["weekdays"]:
                local = datetime(current.year, current.month, current.day, hour, minute, tzinfo=self.tz)
                instants.append(local.astimezone(timezone.utc))
            current += timedelta(days=1)
        return instants

    def _event_instants(self, trigger: dict, room: str) -> list[datetime]:
        event = trigger["event"]
        if event in ("person_arrives", "person_leaves"):
            timeline = self.ctx.person_timeline(trigger["person"])
            if not timeline:
                raise NotTestable("keine Daten für die Person")
            result = []
            for ts, old, new in timeline.transitions():
                if old in UNKNOWN or new in UNKNOWN:
                    continue
                if (event == "person_arrives" and new == "home") or (event == "person_leaves" and old == "home"):
                    result.append(ts)
            return result
        if event == "flat_state":
            if not self.ctx.flat_state:
                raise NotTestable("kein Wohnungszustand aufgezeichnet")
            return [ts for ts, old, new in self.ctx.flat_state.transitions()
                    if new == trigger["value"] and old not in UNKNOWN]
        if event in ("bed_in", "bed_out"):
            if not self.ctx.in_bed:
                raise NotTestable("kein Bett-Sensor aufgezeichnet")
            old_state, new_state = ("off", "on") if event == "bed_in" else ("on", "off")
            return [ts for ts, old, new in self.ctx.in_bed.transitions() if old == old_state and new == new_state]
        if event == "office_day_start":
            if not self.ctx.office_day:
                raise NotTestable("kein Büro-Tag-Sensor aufgezeichnet")
            return [ts for ts, old, new in self.ctx.office_day.transitions() if old == "off" and new == "on"]
        if event == "window_closed":
            windows = [timeline for timeline in self.ctx.windows(room) if timeline]
            if not windows:
                raise NotTestable("keine Fensterkontakte im Raum")
            result = set()
            for timeline in windows:
                for ts, old, new in timeline.transitions():
                    if old == "on" and new == "off" and all(other.value_at(ts) == "off" for other in windows):
                        result.add(ts)
            return sorted(result)
        raise NotTestable(f"unbekanntes Ereignis {event}")

    # --- Bedingungen ------------------------------------------------------------

    def _check_condition_data(self, candidate: Candidate) -> None:
        for condition in candidate.conditions:
            if condition.get("type") in ("signal_state", "signal_range"):
                self._signal(condition["signal"])
            if condition.get("type") == "outside_temp" and not self.ctx.outside_temp:
                raise NotTestable("kein Außensensor aufgezeichnet")
            if condition.get("type") == "person_home" and not self.ctx.person_timeline(condition["person"]):
                raise NotTestable("keine Daten für die Person in der Bedingung")

    def _conditions_hold(self, candidate: Candidate, instant: datetime) -> bool:
        local = instant.astimezone(self.tz)
        for condition in candidate.conditions:
            kind = condition["type"]
            if kind == "weekdays":
                if local.weekday() not in condition["weekdays"]:
                    return False
            elif kind == "time_window":
                if not condition["from"] <= local.strftime("%H:%M") <= condition["to"]:
                    return False
            elif kind == "outside_temp":
                value = self.ctx.outside_temp.numeric_at(instant)
                if value is None:
                    return False
                if condition["op"] == "below" and not value < condition["value"]:
                    return False
                if condition["op"] == "above" and not value > condition["value"]:
                    return False
            elif kind == "person_home":
                state = self.ctx.person_timeline(condition["person"]).value_at(instant)
                if state in UNKNOWN or (state == "home") != condition["home"]:
                    return False
            elif kind == "signal_state":
                state = self._signal(condition["signal"]).timeline.value_at(instant)
                if state is None or (state == condition["value"]) != (condition["op"] == "is"):
                    return False
            elif kind == "signal_range":
                number = self._signal(condition["signal"]).timeline.numeric_at(instant)
                if number is None:
                    return False
                if condition["op"] == "below" and not number < condition["value"]:
                    return False
                if condition["op"] == "above" and not number > condition["value"]:
                    return False
        return True

    # --- Messung ----------------------------------------------------------------

    def _matches(self, item: Intervention, action: dict) -> bool:
        if action["type"] == "preset":
            return item.value == action["value"]
        return abs(float(item.value) - float(action["value"])) <= self.cfg.backtest.setpoint_tolerance

    def _measure(self, candidate: Candidate, instants: list[datetime]) -> BacktestResult:
        params = self.cfg.backtest
        action = candidate.action
        pool = self._pool.get((candidate.room, action["type"]), [])
        times = [item.ts for item in pool]
        fits = [self._matches(item, action) for item in pool]
        total = sum(fits)
        match_window = timedelta(minutes=params.match_window_min)
        contrary_window = timedelta(minutes=params.contrary_window_min)
        reach = max(match_window, contrary_window)
        covered: set[int] = set()
        weeks: set[tuple[int, int]] = set()
        cases = false_triggers = neutral = 0
        for instant in instants:
            low = bisect_right(times, instant)
            high = bisect_right(times, instant + reach)
            matched = [index for index in range(low, high) if fits[index] and times[index] <= instant + match_window]
            contrary = any(not fits[index] and times[index] <= instant + contrary_window
                           for index in range(low, high))
            if matched:
                cases += 1
                covered.update(matched)
                iso = instant.astimezone(self.tz).isocalendar()
                weeks.add((iso[0], iso[1]))
                if contrary:
                    false_triggers += 1
            elif self._is_neutral(candidate, instant):
                neutral += 1
            else:
                false_triggers += 1
        coverage = len(covered) / total if total else 0.0
        effective = len(instants) - neutral
        false_rate = false_triggers / effective if effective else 0.0
        metrics = {"ausloesungen": float(len(instants)), "neutral": float(neutral), "wirksam": float(effective),
                   "faelle": float(cases), "wochen": float(len(weeks)), "eingriffe_gesamt": float(total),
                   "abdeckung": coverage, "fehlausloesungen": false_rate}
        if cases < params.min_faelle or len(weeks) < params.min_wochen:
            return BacktestResult(
                STATUS_FEW,
                f"{cases} Fälle in {len(weeks)} Wochen (nötig: {params.min_faelle} Fälle in {params.min_wochen} Wochen)",
                metrics)
        if coverage >= params.min_abdeckung and false_rate <= params.max_fehlausloesungen:
            return BacktestResult(STATUS_PASSED, "", metrics)
        return BacktestResult(
            STATUS_REJECTED,
            f"Abdeckung {coverage:.2f} (Soll ab {params.min_abdeckung}), "
            f"Fehlauslösungen {false_rate:.2f} (Soll bis {params.max_fehlausloesungen})",
            metrics)
