from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from collector.db import Database
from collector.models import to_utc_iso, utcnow

from .backtest import Backtester, BacktestResult
from .config import AnalysisConfig
from .context import ContextProvider
from .features import build_clusters, build_weekly, data_period, known_presets, room_name
from .interventions import load_interventions
from .llm import request_candidates
from .models import STATUS_OPEN, STATUS_UNTESTABLE, Intervention
from .prompts import build_messages
from .pseudonym import Pseudonyms, build_pseudonyms
from .store import Store
from .vocabulary import ParseContext, parse_candidates, vocabulary_text

MODES = ("weekly", "clusters")


class AnalysisError(Exception):
    pass


class TooFewInterventions(AnalysisError):
    def __init__(self, found: int, needed: int):
        super().__init__(f"Nur {found} manuelle Eingriffe im Zeitraum, mindestens {needed} nötig.")


class NothingToAnalyze(AnalysisError):
    pass


class PrivacyLeak(AnalysisError):
    pass


@dataclass
class Environment:
    start: datetime
    end: datetime
    pseudo: Pseudonyms
    ctx: ContextProvider
    interventions: list[Intervention]


@dataclass
class Prepared:
    mode: str
    env: Environment
    data: dict
    messages: list[dict]
    parse_ctx: ParseContext


@dataclass
class RunSummary:
    run_id: int
    mode: str
    candidates: int
    by_status: dict[str, int]
    cost_usd: float
    tokens_in: int
    tokens_out: int


def load_environment(db: Database, cfg: AnalysisConfig, start: datetime | None = None,
                     end: datetime | None = None) -> Environment:
    period = data_period(db)
    if period is None:
        raise NothingToAnalyze("Die Datenbank enthält keine Zustandsänderungen.")
    start, end = start or period[0], end or period[1]
    pseudo = build_pseudonyms(db)
    return Environment(start, end, pseudo, ContextProvider.from_db(db, cfg, pseudo),
                       load_interventions(db, cfg, start=start, end=end))


def prepare(db: Database, cfg: AnalysisConfig, mode: str, *, start: datetime | None = None,
            end: datetime | None = None) -> Prepared:
    if mode not in MODES:
        raise ValueError(f"unbekannter Modus {mode!r} (erlaubt: {', '.join(MODES)})")
    env = load_environment(db, cfg, start, end)
    if len(env.interventions) < cfg.min_interventions:
        raise TooFewInterventions(len(env.interventions), cfg.min_interventions)
    if mode == "weekly":
        data = build_weekly(env.interventions, env.ctx, cfg, env.pseudo, env.start, env.end)
    else:
        data = build_clusters(env.interventions, env.ctx, cfg, env.pseudo, env.start, env.end)
        if not data["cluster"]:
            raise NothingToAnalyze(
                f"Keine Cluster mit mindestens {cfg.clusters.min_size} Eingriffen gefunden.")
    rooms = {room_name(cfg, area): area for area in {item.room for item in env.interventions}}
    presets = known_presets(db) | {str(item.value) for item in env.interventions if item.kind == "preset"}
    flat_states = env.ctx.flat_state.states()
    parse_ctx = ParseContext(rooms=rooms, pseudo=env.pseudo, presets=frozenset(presets),
                             flat_states=frozenset(flat_states))
    messages = build_messages(mode, data, vocabulary_text(), cfg.max_candidates, presets=sorted(presets),
                              flat_states=sorted(flat_states), rooms=sorted(rooms))
    leaks = env.pseudo.leaks("\n".join(message["content"] for message in messages))
    if leaks:
        raise PrivacyLeak(f"Der zu sendende Text enthält schützenswerte Begriffe: {', '.join(leaks)}")
    return Prepared(mode, env, data, messages, parse_ctx)


async def run_analysis(db: Database, cfg: AnalysisConfig, mode: str, *, api_key: str,
                       start: datetime | None = None, end: datetime | None = None,
                       now=utcnow, completer=request_candidates) -> RunSummary:
    prepared = prepare(db, cfg, mode, start=start, end=end)
    env = prepared.env
    store = Store(db)
    store.save_pseudonyms(env.pseudo.to_rows())
    store.commit()
    prompt_text = "\n\n".join(f"[{message['role']}]\n{message['content']}" for message in prepared.messages)
    prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]
    params = {"von": to_utc_iso(env.start), "bis": to_utc_iso(env.end),
              "eingriffe": len(env.interventions), "prompt_zeichen": len(prompt_text)}
    run_id = store.start_run(mode, cfg.model, params, now())
    try:
        raws, result = await completer(cfg, prepared.messages, api_key)
        candidates = parse_candidates(raws, prepared.parse_ctx)
        tester = Backtester(cfg, env.interventions, env.ctx, env.start, env.end)
        counts: Counter[str] = Counter()
        for candidate in candidates:
            outcome = tester.evaluate(candidate) if candidate.status == STATUS_OPEN else None
            if outcome is not None:
                candidate.status, candidate.note = outcome.status, outcome.note
            candidate_id = store.add_candidate(run_id, candidate)
            if outcome is not None and outcome.metrics:
                store.save_results(candidate_id, outcome.metrics)
            counts[candidate.status] += 1
        store.finish_run(run_id, "abgeschlossen", now(), prompt_hash=prompt_hash, prompt_text=prompt_text,
                         response_text=result.text, tokens_in=result.tokens_in,
                         tokens_out=result.tokens_out, cost_usd=result.cost_usd)
    except Exception as exc:
        store.rollback()
        partial = getattr(exc, "partial", None)
        spent = ({"response_text": partial.text, "tokens_in": partial.tokens_in,
                  "tokens_out": partial.tokens_out, "cost_usd": partial.cost_usd} if partial else {})
        store.finish_run(run_id, "fehlgeschlagen", now(), error=str(exc)[:500], prompt_hash=prompt_hash,
                         prompt_text=prompt_text, **spent)
        raise
    return RunSummary(run_id, mode, len(candidates), dict(counts), result.cost_usd,
                      result.tokens_in, result.tokens_out)


def backtest_candidate(db: Database, cfg: AnalysisConfig, candidate_id: int, *,
                       start: datetime | None = None, end: datetime | None = None) -> BacktestResult:
    store = Store(db)
    candidate = store.get_candidate(candidate_id)
    if candidate.status == STATUS_UNTESTABLE:
        # Gespeichert sind dann die ungeprüften Rohwerte des Modells; nachrechnen ist sinnlos.
        return BacktestResult(STATUS_UNTESTABLE, candidate.note, store.results(candidate_id))
    env = load_environment(db, cfg, start, end)
    outcome = Backtester(cfg, env.interventions, env.ctx, env.start, env.end).evaluate(candidate)
    store.set_candidate_status(candidate_id, outcome.status, outcome.note)
    store.save_results(candidate_id, outcome.metrics)
    store.commit()
    return outcome
