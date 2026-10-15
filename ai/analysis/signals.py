from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from collector.db import Database

from .config import AnalysisConfig
from .features import room_name
from .pseudonym import Pseudonyms
from .store import Store
from .timeline import Timeline, load_timeline

UNKNOWN_STATES = ("unavailable", "unknown", "none", "")
SKIP_KINDS = {"climate", "scene", "button"}      # Thermostate sind das Ziel; Szenen und Tasten haben keinen Verlauf
KIND_NAMES = {"person": "Anwesenheit", "presence": "Präsenz", "contact": "Kontakt", "flat": "Zustand",
              "env": "Sensor", "light": "Licht", "weather": "Wetter", "schedule": "Zeitplan"}
VALUE_KIND_NAMES = {"categorical": "Kategorie", "numeric": "Zahl", "timestamp": "Zeitstempel", "none": "unbrauchbar"}
CONTEXT_NAMES = {"flat_state": "Wohnungszustand", "in_bed": "Jemand im Bett", "office_day": "Bürotag",
                 "outside_temperature": "Außentemperatur"}
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
MAX_DISTINCT = 50                                  # mehr verschiedene Zustände: Freitext, keine Kategorie


@dataclass(frozen=True)
class SignalInfo:
    """Alles, was zum Prüfen von Kandidaten und für den Katalog nötig ist (ohne Zeitreihe)."""

    handle: str
    value_kind: str                               # categorical | numeric | timestamp | none
    description: str
    values: tuple[str, ...] = ()                  # Kategorien ohne "other"
    low: float | None = None
    high: float | None = None
    terciles: tuple[float, float] | None = None


@dataclass
class Signal:
    info: SignalInfo
    entity_id: str
    kind: str
    room: str | None
    timeline: Timeline                            # normalisiert, Unbekanntes als None

    @property
    def handle(self) -> str:
        return self.info.handle


def _is_number(state: str) -> bool:
    try:
        float(state)
    except ValueError:
        return False
    return True


def _known(state: str | None) -> bool:
    return state is not None and state.lower() not in UNKNOWN_STATES


def build_signal(handle: str, entity_id: str, kind: str, room: str | None, raw: Timeline,
                 cfg: AnalysisConfig, pseudo: Pseudonyms) -> Signal:
    """Erkennt die Art des Signals aus den Zuständen und normalisiert den Verlauf."""
    params = cfg.scan
    points = raw.points()
    states = [state for _, state in points if _known(state)]
    description = _describe(entity_id, kind, room, cfg, pseudo)
    is_person = entity_id in pseudo.persons

    def finish(value_kind: str, normalized: list, **extra) -> Signal:
        info = SignalInfo(handle, value_kind, description, **extra)
        return Signal(info, entity_id, kind, room, Timeline(normalized))

    if len(states) < 2:
        return finish("none", [])
    if not is_person and sum(_is_number(s) for s in states) / len(states) >= 0.95:
        numbers = sorted(float(s) for s in states if _is_number(s))
        if numbers[0] == numbers[-1]:
            return finish("none", [])
        terciles = (numbers[len(numbers) // 3], numbers[(2 * len(numbers)) // 3])
        normalized = [(ts, state if _known(state) and _is_number(state) else None) for ts, state in points]
        return finish("numeric", normalized, low=numbers[0], high=numbers[-1], terciles=terciles)
    if not is_person and sum(bool(ISO_RE.match(s)) for s in states) / len(states) >= 0.95:
        normalized = [(ts, state if _known(state) else None) for ts, state in points]
        return finish("timestamp", normalized)
    if is_person:
        normalized = [(ts, ("home" if state == "home" else "away") if _known(state) else None)
                      for ts, state in points]
        values = tuple(sorted({state for _, state in normalized if state is not None}))
        return finish("categorical", normalized, values=values)
    counts = Counter(states)
    if len(counts) > MAX_DISTINCT:
        return finish("none", [])
    frequent = sorted(value for value, count in counts.items() if count >= params.min_value_count)
    if not frequent or len(frequent) > params.max_values:
        return finish("none", [])
    keep = set(frequent)
    normalized = [(ts, (state if state in keep else "other") if _known(state) else None) for ts, state in points]
    return finish("categorical", normalized, values=tuple(frequent))


def _describe(entity_id: str, kind: str, room: str | None, cfg: AnalysisConfig, pseudo: Pseudonyms) -> str:
    """Beschreibung für das Modell: nie aus Entity-ID oder Namen, nur aus Art, Raum und Konfiguration."""
    label = cfg.signal_labels.get(entity_id)
    if label:
        return label
    alias = pseudo.alias_for_person(entity_id)
    if alias:
        return f"Anwesenheit {alias} (home oder away)"
    for key, name in CONTEXT_NAMES.items():
        if cfg.context.get(key) == entity_id:
            return name
    where = f", {room_name(cfg, room)}" if room else ""
    return f"{KIND_NAMES.get(kind, kind)}{where}"


class SignalLibrary:
    def __init__(self, signals: list[Signal]):
        self._signals = sorted(signals, key=lambda signal: signal.handle)
        self._by_handle = {signal.handle: signal for signal in self._signals}

    @classmethod
    def from_db(cls, db: Database, cfg: AnalysisConfig, pseudo: Pseudonyms) -> "SignalLibrary":
        rows = [row for row in db.conn.execute(
            "SELECT entity_id, kind, room FROM entities WHERE active = 1 ORDER BY entity_id")
            if row["kind"] not in SKIP_KINDS]
        store = Store(db)
        handles = store.assign_handles([row["entity_id"] for row in rows])
        signals = []
        for row in rows:
            signal = build_signal(handles[row["entity_id"]], row["entity_id"], row["kind"], row["room"],
                                  load_timeline(db, row["entity_id"]), cfg, pseudo)
            store.update_signal(signal.handle, signal.kind, signal.info.value_kind, signal.info.description)
            signals.append(signal)
        return cls(signals)

    def get(self, handle: str) -> Signal | None:
        return self._by_handle.get(handle)

    def all(self) -> list[Signal]:
        return list(self._signals)

    def usable(self) -> list[Signal]:
        return [signal for signal in self._signals if signal.info.value_kind != "none" and signal.timeline]

    def infos(self) -> dict[str, SignalInfo]:
        return {signal.handle: signal.info for signal in self.usable()}

    def catalog(self) -> list[dict]:
        entries = []
        for signal in self.usable():
            info = signal.info
            entry: dict = {"id": info.handle, "beschreibung": info.description,
                           "art": VALUE_KIND_NAMES[info.value_kind]}
            if info.value_kind == "categorical":
                entry["werte"] = list(info.values)
            elif info.value_kind == "numeric":
                entry["minimum"], entry["maximum"] = info.low, info.high
                entry["terzile"] = list(info.terciles)
            entries.append(entry)
        return entries

    def leaks(self, pseudo: Pseudonyms) -> list[str]:
        text = "\n".join(f"{s.info.description} {' '.join(s.info.values)}" for s in self.usable())
        return pseudo.leaks(text)
