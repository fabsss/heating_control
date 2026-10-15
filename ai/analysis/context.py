from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from collector.db import Database
from collector.models import parse_utc

from .config import AnalysisConfig
from .pseudonym import Pseudonyms
from .timeline import Timeline, load_timeline

UNKNOWN = (None, "unavailable", "unknown")


def _flag(timeline: Timeline, ts: datetime, on: str) -> bool | None:
    value = timeline.value_at(ts)
    return None if value in UNKNOWN else value == on


@dataclass(frozen=True)
class ClimateSeries:
    """Verlauf eines Thermostats: Heizmodus, Sollwert und Preset (aus allen Quellen, nicht nur Eingriffe)."""
    state: Timeline
    temperature: Timeline
    preset: Timeline


def load_climate_series(db: Database, entity_id: str) -> ClimateSeries:
    states, temperatures, presets = [], [], []
    rows = db.conn.execute(
        "SELECT ts_utc, state, json_extract(attrs, '$.temperature') AS temperature, "
        "json_extract(attrs, '$.preset_mode') AS preset FROM state_changes "
        "WHERE entity_id = ? ORDER BY ts_utc, id", (entity_id,))
    for row in rows:
        ts = parse_utc(row["ts_utc"])
        states.append((ts, row["state"]))
        temperatures.append((ts, None if row["temperature"] is None else str(row["temperature"])))
        presets.append((ts, None if row["preset"] is None else str(row["preset"])))
    return ClimateSeries(Timeline(states), Timeline(temperatures), Timeline(presets))


class ContextProvider:
    """Liefert den Kontext (Anwesenheit, Wohnungszustand, Fenster, Wetter) zu einem Zeitpunkt.

    Fehlende Entitäten sind kein Fehler: Die zugehörigen Werte sind `None`.
    """

    signals = None                       # SignalLibrary, vom Lauf gesetzt (Modus scan, allgemeine Kandidaten)

    def __init__(self, pseudo: Pseudonyms, *, persons: dict[str, Timeline] | None = None,
                 flat_state: Timeline | None = None, in_bed: Timeline | None = None,
                 office_day: Timeline | None = None, outside_temp: Timeline | None = None,
                 windows: dict[str, list[Timeline]] | None = None,
                 presence: dict[str, list[Timeline]] | None = None,
                 climate: dict[str, list[ClimateSeries]] | None = None):
        self.pseudo = pseudo
        self._persons = persons or {}
        self.flat_state = flat_state or Timeline()
        self.in_bed = in_bed or Timeline()
        self.office_day = office_day or Timeline()
        self.outside_temp = outside_temp or Timeline()
        self._windows = windows or {}
        self._presence = presence or {}
        self._climate = climate or {}

    @classmethod
    def empty(cls, pseudo: Pseudonyms | None = None) -> "ContextProvider":
        return cls(pseudo or Pseudonyms((), {}, {}))

    @classmethod
    def from_db(cls, db: Database, cfg: AnalysisConfig, pseudo: Pseudonyms) -> "ContextProvider":
        windows: dict[str, list[Timeline]] = defaultdict(list)
        for row in db.conn.execute(
                "SELECT entity_id, room FROM entities WHERE kind = 'contact' AND room IS NOT NULL"):
            windows[row["room"]].append(load_timeline(db, row["entity_id"]))
        presence: dict[str, list[Timeline]] = defaultdict(list)
        for row in db.conn.execute(
                "SELECT entity_id, room FROM entities WHERE kind = 'presence' AND room IS NOT NULL "
                "AND entity_id LIKE 'binary_sensor.%'"):
            presence[row["room"]].append(load_timeline(db, row["entity_id"]))
        climate: dict[str, list[ClimateSeries]] = defaultdict(list)
        for row in db.conn.execute(
                "SELECT entity_id, room FROM entities WHERE kind = 'climate' AND room IS NOT NULL"):
            climate[row["room"]].append(load_climate_series(db, row["entity_id"]))
        return cls(
            pseudo,
            persons={entity_id: load_timeline(db, entity_id) for entity_id in pseudo.persons},
            flat_state=load_timeline(db, cfg.context["flat_state"]),
            in_bed=load_timeline(db, cfg.context["in_bed"]),
            office_day=load_timeline(db, cfg.context["office_day"]),
            outside_temp=load_timeline(db, cfg.context["outside_temperature"]),
            windows=dict(windows),
            presence=dict(presence),
            climate=dict(climate),
        )

    def person_timeline(self, entity_id: str) -> Timeline:
        return self._persons.get(entity_id, Timeline())

    def climate(self, room: str) -> list[ClimateSeries]:
        return self._climate.get(room, [])

    def windows(self, room: str) -> list[Timeline]:
        return self._windows.get(room, [])

    def presence(self, room: str) -> list[Timeline]:
        return self._presence.get(room, [])

    def persons_home(self, ts: datetime) -> dict[str, bool | None]:
        result: dict[str, bool | None] = {}
        for alias, entity_id in self.pseudo.aliases.items():
            timeline = self.person_timeline(entity_id)
            result[alias] = _flag(timeline, ts, "home") if timeline else None
        return {alias: value for alias, value in result.items() if value is not None}

    def snapshot(self, room: str, ts: datetime) -> dict:
        window_states = [tl.value_at(ts) for tl in self.windows(room) if tl]
        if any(state == "on" for state in window_states):
            windows_closed: bool | None = False
        elif window_states and all(state == "off" for state in window_states):
            windows_closed = True
        else:
            windows_closed = None
        presence_states = [tl.value_at(ts) for tl in self.presence(room) if tl]
        if any(state == "on" for state in presence_states):
            room_occupied: bool | None = True
        elif presence_states and all(state == "off" for state in presence_states):
            room_occupied = False
        else:
            room_occupied = None
        flat = self.flat_state.value_at(ts)
        return {
            "persons_home": self.persons_home(ts),
            "flat_state": None if flat in UNKNOWN else flat,
            "in_bed": _flag(self.in_bed, ts, "on"),
            "office_day": _flag(self.office_day, ts, "on"),
            "windows_closed": windows_closed,
            "room_occupied": room_occupied,
            "outside_temp": self.outside_temp.numeric_at(ts),
        }
