from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from collector.db import Database
from collector.models import to_utc_iso, utcnow

from .backtest import Backtester, BacktestResult
from .config import AnalysisConfig
from .context import ContextProvider
from .generic import generic_vocabulary_text
from .langfuse import trace_scope
from .features import build_clusters, build_weekly, data_period, known_presets, room_name
from .interventions import load_interventions
from .llm import request_candidates
from .models import STATUS_OPEN, STATUS_PASSED, STATUS_UNTESTABLE, Association, Intervention
from .prompt_registry import ResolvedPrompt, resolve_all
from .prompts import PROMPT_SPECS, build_messages
from .pseudonym import Pseudonyms, build_pseudonyms
from .scan import association_row, run_scan
from .signals import SignalLibrary
from .store import Store
from .vocabulary import ParseContext, parse_candidates, vocabulary_text

MODES = ("weekly", "clusters", "scan")


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
    signals: SignalLibrary | None = None


@dataclass
class Prepared:
    mode: str
    env: Environment
    data: dict
    messages: list[dict]
    parse_ctx: ParseContext
    associations: list[Association] = field(default_factory=list)


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
    ctx = ContextProvider.from_db(db, cfg, pseudo)
    library = SignalLibrary.from_db(db, cfg, pseudo)
    ctx.signals = library
    return Environment(start, end, pseudo, ctx, load_interventions(db, cfg, start=start, end=end), library)


def build_parse_context(db: Database, env: Environment, cfg: AnalysisConfig, *, scanning: bool) -> ParseContext:
    rooms = {room_name(cfg, area): area for area in {item.room for item in env.interventions}}
    presets = known_presets(db) | {str(item.value) for item in env.interventions if item.kind == "preset"}
    return ParseContext(rooms=rooms, pseudo=env.pseudo, presets=frozenset(presets),
                        flat_states=frozenset(env.ctx.flat_state.states()),
                        signals=env.signals.infos() if scanning else {},
                        max_conditions=2 if scanning else None)


def prepare(db: Database, cfg: AnalysisConfig, mode: str, *, start: datetime | None = None,
            end: datetime | None = None, prompts: dict[str, ResolvedPrompt] | None = None) -> Prepared:
    if mode not in MODES:
        raise ValueError(f"unbekannter Modus {mode!r} (erlaubt: {', '.join(MODES)})")
    env = load_environment(db, cfg, start, end)
    if len(env.interventions) < cfg.min_interventions:
        raise TooFewInterventions(len(env.interventions), cfg.min_interventions)
    associations: list[Association] = []
    if mode == "weekly":
        data = build_weekly(env.interventions, env.ctx, cfg, env.pseudo, env.start, env.end)
    elif mode == "scan":
        associations = run_scan(cfg, env.signals, env.interventions, env.start, env.end)
        if not associations:
            raise NothingToAnalyze(
                "Der Scan fand keine auffälligen Zusammenhänge (Grenzen unter scan: in analysis.yaml).")
        weekly = build_weekly(env.interventions, env.ctx, cfg, env.pseudo, env.start, env.end)
        data = {"zeitraum": weekly["zeitraum"], "signale": env.signals.catalog(),
                "assoziationen": [association_row(item, cfg) for item in associations[:cfg.scan.top_k]],
                "wochenraster": {key: value for key, value in weekly.items() if key != "zeitraum"}}
    else:
        data = build_clusters(env.interventions, env.ctx, cfg, env.pseudo, env.start, env.end)
        if not data["cluster"]:
            raise NothingToAnalyze(
                f"Keine Cluster mit mindestens {cfg.clusters.min_size} Eingriffen gefunden.")
    scanning = mode == "scan"
    parse_ctx = build_parse_context(db, env, cfg, scanning=scanning)
    rooms, presets, flat_states = parse_ctx.rooms, parse_ctx.presets, parse_ctx.flat_states
    vocabulary = generic_vocabulary_text() if scanning else vocabulary_text()
    messages = build_messages(mode, data, vocabulary, cfg.max_candidates, presets=sorted(presets),
                              flat_states=sorted(flat_states), rooms=sorted(rooms), prompts=prompts)
    leaks = env.pseudo.leaks("\n".join(message["content"] for message in messages))
    if leaks:
        raise PrivacyLeak(f"Der zu sendende Text enthält schützenswerte Begriffe: {', '.join(leaks)}")
    return Prepared(mode, env, data, messages, parse_ctx, associations)


async def run_analysis(db: Database, cfg: AnalysisConfig, mode: str, *, api_key: str,
                       start: datetime | None = None, end: datetime | None = None,
                       now=utcnow, completer=request_candidates) -> RunSummary:
    """Ein Lauf ist in Langfuse ein Trace; ohne Langfuse-Einrichtung wirkungslos."""
    async with trace_scope("analysis_run", session_id=f"analyse-{mode}-{now().strftime('%Y%m%d-%H%M%S')}",
                           input={"modus": mode, "modell": cfg.model},
                           metadata={"mode": mode, "model": cfg.model}) as trace:
        summary = await _run_analysis(db, cfg, mode, api_key=api_key, start=start, end=end, now=now,
                                      completer=completer, trace=trace)
        trace.output = {"run_id": summary.run_id, "kandidaten": summary.candidates,
                        "status": summary.by_status, "kosten_usd": summary.cost_usd,
                        "tokens_in": summary.tokens_in, "tokens_out": summary.tokens_out}
        total = summary.candidates
        if total:
            # Qualität des Laufs: Anteil der Kandidaten, die den Backtest bestehen, und Anteil prüfbarer
            passed = summary.by_status.get(STATUS_PASSED, 0)
            trace.score("kandidaten_bestanden", passed / total, f"{passed} von {total}")
            trace.score("kandidaten_pruefbar", 1 - summary.by_status.get(STATUS_UNTESTABLE, 0) / total)
        return summary


async def _run_analysis(db: Database, cfg: AnalysisConfig, mode: str, *, api_key: str, start, end, now,
                        completer, trace) -> RunSummary:
    prompts = await resolve_all(PROMPT_SPECS)
    for resolved in prompts.values():
        if resolved.key in ("system", mode):
            trace.use_prompt(resolved.key, resolved.info())
    with trace.step("prepare", input={"modus": mode}) as step:
        prepared = prepare(db, cfg, mode, start=start, end=end, prompts=prompts)
        step["output"] = {"eingriffe": len(prepared.env.interventions),
                          "assoziationen": len(prepared.associations),
                          "prompt_zeichen": sum(len(m["content"]) for m in prepared.messages)}
    env = prepared.env
    store = Store(db)
    store.save_pseudonyms(env.pseudo.to_rows())
    store.commit()
    prompt_text = "\n\n".join(f"[{message['role']}]\n{message['content']}" for message in prepared.messages)
    prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]
    params = {"von": to_utc_iso(env.start), "bis": to_utc_iso(env.end),
              "eingriffe": len(env.interventions), "prompt_zeichen": len(prompt_text)}
    used = {key: resolved.version for key, resolved in prompts.items()
            if key in ("system", mode) and resolved.version is not None}
    if used:
        params["prompt_versionen"] = used   # Langfuse-Versionen; ohne Angabe gilt der Prompt aus dem Code
    run_id = store.start_run(mode, cfg.model, params, now())
    trace.metadata["run_id"] = run_id
    try:
        raws, result = await completer(cfg, prepared.messages, api_key)
        store.save_associations(run_id, prepared.associations)
        with trace.step("parse_candidates", input=raws) as step:
            candidates = parse_candidates(raws, prepared.parse_ctx)
            step["output"] = [{"room": c.room, "trigger": c.trigger, "conditions": c.conditions,
                               "action": c.action, "hypothesis": c.hypothesis, "status": c.status,
                               "note": c.note} for c in candidates]
        tester = Backtester(cfg, env.interventions, env.ctx, env.start, env.end)
        counts: Counter[str] = Counter()
        verdicts = []
        with trace.step("backtest") as step:
            for candidate in candidates:
                outcome = tester.evaluate(candidate) if candidate.status == STATUS_OPEN else None
                if outcome is not None:
                    candidate.status, candidate.note = outcome.status, outcome.note
                candidate_id = store.add_candidate(run_id, candidate)
                if outcome is not None and outcome.metrics:
                    store.save_results(candidate_id, outcome.metrics)
                counts[candidate.status] += 1
                verdicts.append({"id": candidate_id, "room": candidate.room, "status": candidate.status,
                                 "note": candidate.note})
            step["output"] = verdicts
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


@dataclass
class ManualSummary:
    run_id: int
    candidate_ids: list[int]
    by_status: dict[str, int]


def add_manual_candidates(db: Database, cfg: AnalysisConfig, raws: list, *, now=utcnow) -> ManualSummary:
    """Prüft selbst formulierte Kandidaten wie die des Modells (gleiches Vokabular, gleicher Backtest), ohne Aufruf."""
    if not raws:
        raise AnalysisError("Kein Kandidat angegeben.")
    env = load_environment(db, cfg)
    store = Store(db)
    store.save_pseudonyms(env.pseudo.to_rows())
    store.commit()
    candidates = parse_candidates(raws, build_parse_context(db, env, cfg, scanning=True))
    tester = Backtester(cfg, env.interventions, env.ctx, env.start, env.end)
    run_id = store.start_run("manuell", "manuell", {"von": to_utc_iso(env.start), "bis": to_utc_iso(env.end),
                                                    "eingriffe": len(env.interventions)}, now())
    counts: Counter[str] = Counter()
    ids: list[int] = []
    try:
        for candidate in candidates:
            outcome = tester.evaluate(candidate) if candidate.status == STATUS_OPEN else None
            if outcome is not None:
                candidate.status, candidate.note = outcome.status, outcome.note
            ids.append(store.add_candidate(run_id, candidate))
            if outcome is not None and outcome.metrics:
                store.save_results(ids[-1], outcome.metrics)
            counts[candidate.status] += 1
        store.finish_run(run_id, "abgeschlossen", now())
    except Exception as exc:
        store.rollback()
        store.finish_run(run_id, "fehlgeschlagen", now(), error=str(exc)[:500])
        raise
    return ManualSummary(run_id, ids, dict(counts))


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
