from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from collector.db import Database
from collector.models import parse_utc, to_utc_iso

from .config import ExecutionConfig
from .models import Deployment, RuleEvent, Stats
from .store import ExecStore

RELEVANT = {"temperature", "preset_mode"}
UNSCORED_REASONS = ("wohnungszustand", "nicht_erreichbar")
OWN_CHANGE_WINDOW_S = 15.0         # eine eigene Schaltung (auch das kurze Einschalten) liegt so nah an der Auslösung


@dataclass(frozen=True)
class Verdict:
    outcome: str
    window_start: datetime
    window_end: datetime
    lage: str | None = None
    abstand_min: float | None = None
    deviation: str | None = None


@dataclass(frozen=True)
class _Row:
    ts: datetime
    actor: str
    changed: frozenset
    new: dict
    automation: str | None


def same_target(target: dict, new: dict, tolerance: float) -> bool:
    if target.get("kind") == "preset":
        return new.get("preset_mode") == target.get("value")
    temperature = new.get("temperature")
    if temperature is None:
        return False
    try:
        return abs(float(temperature) - float(target["value"])) <= tolerance
    except (TypeError, ValueError, KeyError):
        return False


def _deviation(target: dict, new: dict) -> str:
    if target.get("kind") == "preset":
        return f"preset:{new.get('preset_mode')}"
    temperature = new.get("temperature")
    return f"{float(temperature):g}" if temperature is not None else "?"


def _load(db: Database, entity_id: str, start: datetime, end: datetime) -> list[_Row]:
    rows = db.conn.execute(
        "SELECT ts_utc, actor_class, changed_attrs, new, origin_automation FROM manual_actions "
        "WHERE entity_id = ? AND ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc",
        (entity_id, to_utc_iso(start), to_utc_iso(end))).fetchall()
    return [_Row(parse_utc(row["ts_utc"]), row["actor_class"], frozenset(row["changed_attrs"].split(",")),
                 json.loads(row["new"]), row["origin_automation"]) for row in rows]


def evaluate(db: Database, event: RuleEvent, deployment: Deployment, cfg: ExecutionConfig) -> Verdict | None:
    ts = parse_utc(event.ts_utc)
    start = ts - timedelta(minutes=cfg.eval_before_min)
    end = ts + timedelta(minutes=cfg.eval_after_min)
    if event.reason in UNSCORED_REASONS:
        return None
    if event.reason == "bereits_erreicht":
        return Verdict("ohne_wirkung", start, end)
    rows = _load(db, deployment.climate_entity, start, end)
    humans = [row for row in rows if row.actor in ("manual", "device") and row.changed & RELEVANT]
    own_change = event.result == "ausgefuehrt"
    others = [row for row in rows if row.actor == "automation" and row.automation != deployment.ha_automation
              and not (own_change and 0 <= (row.ts - ts).total_seconds() <= OWN_CHANGE_WINDOW_S)]
    target = event.target

    if own_change:
        after = [row for row in humans if row.ts > ts]
        if after:
            last = after[-1]
            if any(ts < other.ts < last.ts for other in others):
                return Verdict("unterbrochen", start, end)
            distance = (last.ts - ts).total_seconds() / 60
            if same_target(target, last.new, cfg.target_tolerance):
                return Verdict("bestaetigt", start, end, "nachher", distance)
            return Verdict("korrigiert", start, end, "nachher", distance, _deviation(target, last.new))
        if any(other.ts > ts for other in others):
            return Verdict("unterbrochen", start, end)
        return Verdict("bestaetigt", start, end)

    if not humans:
        if any(other.ts > ts for other in others):
            return Verdict("unterbrochen", start, end)
        return Verdict("neutral", start, end)
    last = humans[-1]
    lage = "vorher" if last.ts < ts else "nachher"
    distance = (last.ts - ts).total_seconds() / 60
    if lage == "nachher":
        interrupted = any(ts < other.ts < last.ts for other in others)
    else:
        interrupted = any(last.ts < other.ts < ts for other in others)
    if interrupted:
        return Verdict("unterbrochen", start, end, lage, distance)
    if same_target(target, last.new, cfg.target_tolerance):
        return Verdict("treffer", start, end, lage, distance)
    return Verdict("fehlausloesung", start, end, lage, distance, _deviation(target, last.new))


def rule_stats(store: ExecStore, rule_id: str, cfg: ExecutionConfig) -> Stats:
    rows = store.stats_rows(rule_id)
    outcomes = Counter(row["outcome"] for row in rows if row["outcome"])
    reasons = Counter(row["reason"] for row in rows if row["result"] == "nicht_ausgefuehrt" and row["reason"])
    good = outcomes["treffer"] + outcomes["bestaetigt"]
    bad = outcomes["fehlausloesung"] + outcomes["korrigiert"]
    scored = good + bad
    shadow_scored = outcomes["treffer"] + outcomes["fehlausloesung"]
    active_scored = outcomes["bestaetigt"] + outcomes["korrigiert"]
    timed = [row for row in rows if row["outcome"] in ("treffer", "fehlausloesung", "korrigiert", "bestaetigt")
             and row["abstand_min"] is not None]
    distances = [row["abstand_min"] for row in timed]
    deviations = Counter(row["deviation"] for row in rows
                         if row["deviation"] and row["outcome"] in ("fehlausloesung", "korrigiert"))
    if scored < cfg.min_scored:
        maturity = "zu_wenig_daten"
    else:
        maturity = "gruen" if bad / scored < cfg.max_fail_rate else "rot"
    return Stats(
        events=len(rows), scored=scored, good=good, treffer=outcomes["treffer"],
        fehlausloesung=outcomes["fehlausloesung"], neutral=outcomes["neutral"], bestaetigt=outcomes["bestaetigt"],
        korrigiert=outcomes["korrigiert"], unterbrochen=outcomes["unterbrochen"],
        ohne_wirkung=outcomes["ohne_wirkung"], ausgebremst=reasons["handeingriff"], reasons=dict(reasons),
        trefferquote=outcomes["treffer"] / shadow_scored if shadow_scored else None,
        korrekturquote=outcomes["korrigiert"] / active_scored if active_scored else None,
        median_abstand_min=statistics.median(distances) if distances else None,
        anteil_vorher=(sum(1 for row in timed if row["lage"] == "vorher") / len(timed)) if timed else None,
        abweichung=deviations.most_common(1)[0][0] if deviations else None,
        reife=maturity,
        text=f"{good} von {scored} gewertet richtig, {outcomes['neutral']} ohne Reaktion, "
             f"{outcomes['ohne_wirkung']} ohne Wirkung",
    )
