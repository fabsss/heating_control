from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from collector.db import Database
from collector.models import parse_utc

from .candidates import describe, signature
from .config import AnalysisConfigError, load_analysis_config
from .presence import analyze as analyze_presence, render as render_presence
from .llm import LlmError, estimate_cost, with_live_prices
from .pseudonym import build_pseudonyms
from .runner import MODES, AnalysisError, backtest_candidate, prepare, run_analysis
from .store import Store

STATUSES = ("bestanden", "zu_wenig_belege", "abgelehnt", "nicht_pruefbar")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="analysis", description="KI-Analyse: Regelkandidaten für die Heizung")
    parser.add_argument("--config", default=os.environ.get("ANALYSIS_CONFIG", "/app/config/analysis.yaml"))
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "/data/home_ai.sqlite"))
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="einen Analyse-Lauf starten")
    run.add_argument("--mode", required=True, choices=MODES)
    run.add_argument("--from", dest="since")
    run.add_argument("--to", dest="until")
    run.add_argument("--dry-run", action="store_true", help="Text anzeigen, nichts senden")
    compare = sub.add_parser("compare", help="Läufe gegenüberstellen")
    compare.add_argument("run_ids", nargs="*", type=int)
    listing = sub.add_parser("list", help="Kandidaten anzeigen")
    listing.add_argument("--run", dest="run_id", type=int)
    listing.add_argument("--status", choices=STATUSES)
    sub.add_parser("presence", help="Anwesenheit und manuelle Eingriffe gegenüberstellen")
    backtest = sub.add_parser("backtest", help="Backtest eines Kandidaten erneut rechnen")
    backtest.add_argument("candidate_id", type=int)
    backtest.add_argument("--from", dest="since")
    backtest.add_argument("--to", dest="until")
    return parser


def _period(args) -> tuple:
    return (parse_utc(args.since) if args.since else None, parse_utc(args.until) if args.until else None)


def _cmd_run(cfg, db: Database, args) -> int:
    start, end = _period(args)
    cfg = asyncio.run(with_live_prices(cfg))
    source = {"openrouter": "OpenRouter", "env": "Umgebungsvariablen", "datei": "analysis.yaml"}[cfg.price_source]
    model_source = "Umgebungsvariable OPENROUTER_MODEL" if cfg.model_source == "env" else "analysis.yaml"
    print(f"Modell: {cfg.model} ({model_source})", file=sys.stderr)
    print(f"Preise: {cfg.price_input:.2f} / {cfg.price_output:.2f} USD je Million Token ({source})", file=sys.stderr)
    if args.dry_run:
        prepared = prepare(db, cfg, args.mode, start=start, end=end)
        for message in prepared.messages:
            print(f"--- {message['role']} ---")
            print(message["content"])
        print(f"--- {len(prepared.env.interventions)} Eingriffe, geschätzte Kosten höchstens "
              f"{estimate_cost(prepared.messages, cfg):.2f} USD, kein Aufruf ---")
        return 0
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise AnalysisError("Umgebungsvariable OPENROUTER_API_KEY fehlt")
    summary = asyncio.run(run_analysis(db, cfg, args.mode, api_key=api_key, start=start, end=end))
    print(f"Lauf {summary.run_id} ({summary.mode}) abgeschlossen: {summary.candidates} Kandidaten")
    for status in STATUSES:
        print(f"  {status}: {summary.by_status.get(status, 0)}")
    print(f"Kosten: {summary.cost_usd:.4f} USD ({summary.tokens_in} Token ein, {summary.tokens_out} aus)")
    return 0


def _latest_per_mode(store: Store) -> list:
    latest: dict[str, object] = {}
    for row in store.list_runs():
        if row["status"] == "abgeschlossen":
            latest[row["mode"]] = row
    return [latest[mode] for mode in MODES if mode in latest]


def _cmd_compare(store: Store, args) -> int:
    if args.run_ids:
        rows = []
        for run_id in args.run_ids:
            row = store.get_run(run_id)
            if row is None:
                raise AnalysisError(f"Lauf {run_id} existiert nicht")
            rows.append(row)
    else:
        rows = _latest_per_mode(store)
    if not rows:
        print("Keine abgeschlossenen Läufe zum Vergleichen.")
        return 1
    print(f"{'Lauf':>4}  {'Modus':<9} {'Modell':<28} {'Kandidaten':>10} {'bestanden':>9} "
          f"{'zu_wenig_belege':>15} {'abgelehnt':>9} {'nicht_pruefbar':>14} {'Kosten':>8}")
    signatures: list[set] = []
    for row in rows:
        candidates = store.list_candidates(run_id=row["id"])
        counts = {status: sum(1 for item in candidates if item.status == status) for status in STATUSES}
        cost = row["cost_usd"] or 0.0
        print(f"{row['id']:>4}  {row['mode']:<9} {row['model']:<28} {len(candidates):>10} "
              f"{counts['bestanden']:>9} {counts['zu_wenig_belege']:>15} {counts['abgelehnt']:>9} "
              f"{counts['nicht_pruefbar']:>14} {cost:>8.4f}")
        signatures.append({key for key in map(signature, candidates) if key is not None})
    if len(rows) == 2:
        first, second = rows[0]["id"], rows[1]["id"]
        both = signatures[0] & signatures[1]
        print(f"Überlappung Lauf {first} / Lauf {second}: gemeinsam {len(both)}, "
              f"nur Lauf {first}: {len(signatures[0] - both)}, nur Lauf {second}: {len(signatures[1] - both)}")
    return 0


def _cmd_list(cfg, db: Database, store: Store, args) -> int:
    pseudo = build_pseudonyms(db)
    for item in store.list_candidates(run_id=args.run_id, status=args.status):
        room, when, action = describe(item, cfg, pseudo)
        line = f"{item.id:>4}  {room}  {when}  ->  {action}  [{item.status}]"
        metrics = store.results(item.id)
        if metrics:
            line += (f"  abdeckung={metrics['abdeckung']:.2f} fehlausloesungen={metrics['fehlausloesungen']:.2f} "
                     f"faelle={metrics['faelle']:.0f} wochen={metrics['wochen']:.0f}")
            if "wirksam" in metrics:
                line += f" wirksam={metrics['wirksam']:.0f} neutral={metrics['neutral']:.0f}"
        print(line)
    return 0


def _cmd_presence(cfg, db: Database) -> int:
    print(render_presence(analyze_presence(db, cfg)))
    return 0


def _cmd_backtest(cfg, db: Database, args) -> int:
    start, end = _period(args)
    outcome = backtest_candidate(db, cfg, args.candidate_id, start=start, end=end)
    print(f"Kandidat {args.candidate_id}: {outcome.status}")
    if outcome.note:
        print(f"  {outcome.note}")
    for name, value in outcome.metrics.items():
        print(f"  {name}: {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        cfg = load_analysis_config(args.config)
        db = Database(args.db)
        try:
            store = Store(db)
            if args.command == "run":
                return _cmd_run(cfg, db, args)
            if args.command == "presence":
                return _cmd_presence(cfg, db)
            if args.command == "compare":
                return _cmd_compare(store, args)
            if args.command == "list":
                return _cmd_list(cfg, db, store, args)
            return _cmd_backtest(cfg, db, args)
        finally:
            db.close()
    except (AnalysisConfigError, AnalysisError, LlmError, FileNotFoundError, KeyError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
