from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Deployment:
    rule_id: str
    candidate_id: int
    room: str                       # area_id
    ha_automation: str              # automation.<...>
    config_id: str
    climate_entity: str
    state: str                      # schatten | aktiv | entfernt
    blueprint_version: str
    description: str
    created_at: str
    removed_at: str | None = None


@dataclass(frozen=True)
class RuleEvent:
    rule_id: str
    ts_utc: str
    mode: str                       # aktiv | schatten
    result: str                     # ausgefuehrt | nicht_ausgefuehrt
    reason: str
    target: dict
    current_state: dict
    payload: str
    id: int | None = None


@dataclass(frozen=True)
class Stats:
    events: int
    scored: int
    good: int
    treffer: int
    fehlausloesung: int
    neutral: int
    bestaetigt: int
    korrigiert: int
    unterbrochen: int
    ohne_wirkung: int
    ausgebremst: int
    reasons: dict = field(default_factory=dict)
    trefferquote: float | None = None
    korrekturquote: float | None = None
    median_abstand_min: float | None = None
    anteil_vorher: float | None = None
    abweichung: str | None = None
    reife: str = "zu_wenig_daten"       # gruen | rot | zu_wenig_daten
    text: str = ""

    def to_payload(self) -> dict:
        def pct(value):
            return None if value is None else round(100 * value, 1)
        return {
            "events": self.events, "scored": self.scored, "good": self.good, "treffer": self.treffer,
            "fehlausloesung": self.fehlausloesung, "neutral": self.neutral, "bestaetigt": self.bestaetigt,
            "korrigiert": self.korrigiert, "unterbrochen": self.unterbrochen, "ohne_wirkung": self.ohne_wirkung,
            "ausgebremst": self.ausgebremst, "reasons": self.reasons,
            "trefferquote_pct": pct(self.trefferquote), "korrekturquote_pct": pct(self.korrekturquote),
            "median_abstand_min": self.median_abstand_min, "anteil_vorher_pct": pct(self.anteil_vorher),
            "abweichung": self.abweichung, "reife": self.reife, "text": self.text,
        }
