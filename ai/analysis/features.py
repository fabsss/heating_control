from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from collector.db import Database
from collector.models import parse_utc

from .config import AnalysisConfig
from .context import UNKNOWN, ContextProvider
from .models import Intervention
from .pseudonym import Pseudonyms

WEEKDAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def room_name(cfg: AnalysisConfig, area_id: str) -> str:
    return cfg.room_names.get(area_id, area_id)


def data_period(db: Database) -> tuple[datetime, datetime] | None:
    row = db.conn.execute("SELECT MIN(ts_utc) AS first, MAX(ts_utc) AS last FROM state_changes").fetchone()
    if row["first"] is None:
        return None
    return parse_utc(row["first"]), parse_utc(row["last"]) + timedelta(seconds=1)


def known_presets(db: Database) -> set[str]:
    rows = db.conn.execute(
        "SELECT DISTINCT json_extract(sc.attrs, '$.preset_mode') AS preset FROM state_changes sc "
        "JOIN entities e ON e.entity_id = sc.entity_id WHERE e.kind = 'climate'")
    return {row["preset"] for row in rows if row["preset"]}


def _value_key(item: Intervention) -> float | str:
    return item.value if item.kind == "preset" else round(float(item.value) * 2) / 2


def _period(cfg: AnalysisConfig, start: datetime, end: datetime) -> dict:
    tz = ZoneInfo(cfg.timezone)
    return {"von": start.astimezone(tz).date().isoformat(), "bis": end.astimezone(tz).date().isoformat()}


def _share(values: list[bool]) -> float:
    return round(sum(values) / len(values), 2)


def build_weekly(interventions: list[Intervention], ctx: ContextProvider, cfg: AnalysisConfig,
                 pseudo: Pseudonyms, start: datetime, end: datetime) -> dict:
    """Raster je Raum, Wochentag und Stunde (lokale Zeit); nur Zellen mit Eingriffen."""
    tz = ZoneInfo(cfg.timezone)
    cells: dict[tuple[str, int, int], list[Intervention]] = defaultdict(list)
    for item in interventions:
        local = item.ts.astimezone(tz)
        cells[(item.room, local.weekday(), local.hour)].append(item)
    rooms: dict[str, list[dict]] = defaultdict(list)
    for (room, weekday, hour), items in sorted(cells.items()):
        actions = Counter((item.kind, _value_key(item)) for item in items)
        (kind, value), count = actions.most_common(1)[0]
        home: dict[str, list[bool]] = defaultdict(list)
        for item in items:
            for alias, is_home in ctx.snapshot(room, item.ts)["persons_home"].items():
                home[alias].append(is_home)
        rooms[room_name(cfg, room)].append({
            "wochentag": WEEKDAY_NAMES[weekday],
            "stunde": hour,
            "eingriffe": len(items),
            "haeufigste_aktion": {"type": kind, "value": value},
            "anteil_haeufigste": round(count / len(items), 2),
            "personen_zuhause_anteil": {alias: _share(values) for alias, values in sorted(home.items())},
        })
    flat: dict[int, Counter] = defaultdict(Counter)
    home_hours: dict[int, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    moment = start.replace(minute=0, second=0, microsecond=0)
    while moment < end:
        weekday = moment.astimezone(tz).weekday()
        state = ctx.flat_state.value_at(moment)
        if state not in UNKNOWN:
            flat[weekday][state] += 1
        for alias, is_home in ctx.persons_home(moment).items():
            home_hours[weekday][alias].append(is_home)
        moment += timedelta(hours=1)
    weekdays: dict[str, dict] = {}
    for number, name in enumerate(WEEKDAY_NAMES):
        entry: dict = {}
        if flat[number]:
            total = sum(flat[number].values())
            entry["wohnungszustand_anteile"] = {state: round(count / total, 2)
                                                for state, count in sorted(flat[number].items())}
        if home_hours[number]:
            entry["personen_zuhause_anteil"] = {alias: _share(values)
                                                for alias, values in sorted(home_hours[number].items())}
        weekdays[name] = entry
    return {
        "zeitraum": _period(cfg, start, end),
        "eingriffe_gesamt": len(interventions),
        "raeume": dict(rooms),
        "wochentage": weekdays,
    }


def _context_shares(snapshots: list[dict]) -> dict:
    shares: dict = {}
    home: dict[str, list[bool]] = defaultdict(list)
    for snap in snapshots:
        for alias, is_home in snap["persons_home"].items():
            home[alias].append(is_home)
    if home:
        shares["personen_zuhause_anteil"] = {alias: _share(values) for alias, values in sorted(home.items())}
    states = [snap["flat_state"] for snap in snapshots if snap["flat_state"] is not None]
    if states:
        counts = Counter(states)
        shares["wohnungszustand_anteile"] = {state: round(count / len(states), 2)
                                             for state, count in sorted(counts.items())}
    for key, label in (("in_bed", "im_bett_anteil"), ("office_day", "buerotag_anteil"),
                       ("windows_closed", "fenster_zu_anteil"), ("room_occupied", "raum_belegt_anteil")):
        values = [snap[key] for snap in snapshots if snap[key] is not None]
        if values:
            shares[label] = _share(values)
    temperatures = [snap["outside_temp"] for snap in snapshots if snap["outside_temp"] is not None]
    buckets = Counter("unter_8" if t < 8 else "8_bis_15" if t <= 15 else "ueber_15" for t in temperatures)
    shares["aussentemperatur"] = {name: round(count / len(temperatures), 2)
                                  for name, count in sorted(buckets.items())}
    return shares


def _clock(minutes: float) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def build_clusters(interventions: list[Intervention], ctx: ContextProvider, cfg: AnalysisConfig,
                   pseudo: Pseudonyms, start: datetime, end: datetime) -> dict:
    """Bündelt Eingriffe nach Raum, Aktion, Wochentagsgruppe und Uhrzeit und hängt den Kontext an."""
    tz = ZoneInfo(cfg.timezone)
    groups: dict[tuple, list[tuple[int, Intervention]]] = defaultdict(list)
    for item in interventions:
        local = item.ts.astimezone(tz)
        group = next((name for name, days in cfg.clusters.weekday_groups.items()
                      if local.weekday() in days), None)
        if group is None:
            continue
        groups[(item.room, item.kind, _value_key(item), group)].append((local.hour * 60 + local.minute, item))
    clusters: list[dict] = []
    for (room, kind, value, group), members in sorted(groups.items(), key=lambda entry: str(entry[0])):
        members.sort(key=lambda entry: entry[0])
        index = 0
        while index < len(members):
            first_minute = members[index][0]
            stop = index
            while stop < len(members) and members[stop][0] - first_minute <= cfg.clusters.window_min:
                stop += 1
            chosen = members[index:stop]
            index = stop
            if len(chosen) < cfg.clusters.min_size:
                continue
            minutes = [minute for minute, _ in chosen]
            weeks = {item.ts.astimezone(tz).isocalendar()[:2] for _, item in chosen}
            clusters.append({
                "raum": room_name(cfg, room),
                "aktion": {"type": kind, "value": value},
                "wochentage": group,
                "uhrzeit": {"von": _clock(min(minutes)), "median": _clock(median(minutes)),
                            "bis": _clock(max(minutes))},
                "anzahl": len(chosen),
                "wochen": len(weeks),
                "kontext": _context_shares([ctx.snapshot(room, item.ts) for _, item in chosen]),
            })
    return {"zeitraum": _period(cfg, start, end), "cluster": clusters}
