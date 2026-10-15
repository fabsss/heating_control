from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from collector.db import Database
from collector.models import parse_utc, utcnow

from .candidates import describe, signature
from .config import AnalysisConfigError, load_analysis_config
from .events import EventSpec, event_text
from .features import room_name
from .presence import analyze as analyze_presence, render as render_presence
from .llm import LlmError, estimate_cost, with_live_prices
from .pseudonym import build_pseudonyms
from .runner import (MODES, AnalysisError, TooFewInterventions, add_manual_candidates, backtest_candidate,
                     load_environment, prepare, run_analysis)
from .scan import run_scan
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
    listing.add_argument("--latest", action="store_true", help="nur der neueste Lauf (alle Status)")
    presence = sub.add_parser("presence", help="Anwesenheit und manuelle Eingriffe gegenüberstellen")
    presence.add_argument("--classes", default="45,180",
                          help="Grenzen der Abwesenheitsklassen in Minuten, Komma getrennt (Standard 45,180)")
    sub.add_parser("signals", help="aufgezeichnete Signale mit Kennung und Art anzeigen")
    scan = sub.add_parser("scan", help="Assoziationen zwischen Ereignissen und Eingriffen suchen (ohne Modell)")
    scan.add_argument("--room", help="nur dieser Raum (Klartextname)")
    scan.add_argument("--top", type=int, default=30, help="so viele Zeilen anzeigen (Standard 30)")
    add = sub.add_parser("add", help="selbst formulierte Kandidaten prüfen (Backtest, ohne Modell, ohne Kosten)")
    add.add_argument("--json", help="Kandidat als JSON-Objekt oder Liste; ohne Angabe von der Standardeingabe lesen")
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
    for left in range(len(rows)):
        for right in range(left + 1, len(rows)):
            first, second = rows[left]["id"], rows[right]["id"]
            both = signatures[left] & signatures[right]
            print(f"Überlappung Lauf {first} / Lauf {second}: gemeinsam {len(both)}, "
                  f"nur Lauf {first}: {len(signatures[left] - both)}, nur Lauf {second}: {len(signatures[right] - both)}")
    return 0


def _cmd_list(cfg, db: Database, store: Store, args) -> int:
    pseudo = build_pseudonyms(db)
    signals = store.signal_descriptions()
    run_id = args.run_id
    if args.latest:
        with_candidates = [row["id"] for row in store.list_runs() if store.list_candidates(run_id=row["id"])]
        if not with_candidates:
            print("Noch kein Lauf vorhanden.")
            return 1
        run_id = with_candidates[-1]
    for item in store.list_candidates(run_id=run_id, status=args.status):
        room, when, action = describe(item, cfg, pseudo, signals)
        line = f"{item.id:>4}  {room}  {when}  ->  {action}  [{item.status}]"
        if item.note and item.status != "bestanden":
            line += f"  grund: {item.note}"
        metrics = store.results(item.id)
        if metrics:
            line += (f"  abdeckung={metrics['abdeckung']:.2f} fehlausloesungen={metrics['fehlausloesungen']:.2f} "
                     f"faelle={metrics['faelle']:.0f} wochen={metrics['wochen']:.0f}")
            if "wirksam" in metrics:
                line += f" wirksam={metrics['wirksam']:.0f} neutral={metrics['neutral']:.0f}"
        print(line)
    return 0


def _cmd_presence(cfg, db: Database, args) -> int:
    try:
        edges = tuple(sorted({int(part) for part in args.classes.split(",") if part.strip()}))
    except ValueError as exc:
        raise AnalysisError(f"--classes erwartet Zahlen, nicht {args.classes!r}") from exc
    if not edges or edges[0] <= 0:
        raise AnalysisError("--classes braucht mindestens eine positive Zahl")
    print(render_presence(analyze_presence(db, cfg, edges=edges)))
    return 0


def _cmd_signals(db: Database, store: Store) -> int:
    rows = store.list_signals()
    if not rows:
        print("Noch keine Signale. Sie entstehen beim ersten Scan oder Lauf.")
        return 1
    print(f"{'Kennung':<8} {'Art':<12} {'Kind':<9} {'Entität (nur lokal)':<52} Beschreibung")
    for row in rows:
        print(f"{row['handle']:<8} {row['value_kind']:<12} {row['kind']:<9} {row['entity_id']:<52} {row['description']}")
    return 0


def _cmd_scan(cfg, db: Database, store: Store, args) -> int:
    env = load_environment(db, cfg)
    if len(env.interventions) < cfg.min_interventions:
        raise TooFewInterventions(len(env.interventions), cfg.min_interventions)
    found = run_scan(cfg, env.signals, env.interventions, env.start, env.end)
    run_id = store.start_run("scan-only", "-", {"eingriffe": len(env.interventions)}, utcnow())
    store.save_associations(run_id, found)
    store.finish_run(run_id, "abgeschlossen", utcnow())
    if args.room:
        area = {name: area for area, name in cfg.room_names.items()}.get(args.room)
        if area is None:
            raise AnalysisError(f"Unbekannter Raum {args.room!r} (bekannt: {', '.join(sorted(cfg.room_names.values()))})")
        found = [item for item in found if item.room == area]
    if not found:
        print("Keine auffälligen Zusammenhänge gefunden.")
        return 0
    print(f"{len(env.interventions)} Eingriffe, {len(env.signals.usable())} nutzbare Signale. Treffer: Ereignisse mit Eingriff "
          "im Fenster (Kontrolle: dieselbe Uhrzeit an anderen Tagen).")
    shown = found[:args.top]
    for item in shown:
        info = env.signals.get(item.signal).info
        spec = EventSpec(item.signal, item.event, item.value, item.prev_class)
        room = "alle Räume" if item.room is None else room_name(cfg, item.room)
        print(f"{item.signal} {info.description}: {event_text(spec)}\n"
              f"      {room}, Fenster {item.win_start:+d} bis {item.win_end:+d} min: {item.hits} von {item.events} "
              f"({100 * item.hits / item.events:.0f}%), Kontrolle {100 * item.control_hits / item.control_events:.0f}%, "
              f"lift {item.lift:.1f}, q {item.q:.3g}" + (f", {item.typical_action}" if item.typical_action else ""))
    print(f"{len(shown)} von {len(found)} Zeilen. Lauf {run_id} gespeichert.")
    return 0


def _candidates_from_text(text: str) -> list:
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise AnalysisError(f"Die Eingabe ist kein gültiges JSON: {exc}") from exc
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list) or not parsed:
        raise AnalysisError("Erwartet wird ein Kandidat (JSON-Objekt) oder eine nicht leere Liste davon.")
    return parsed


def _cmd_add(cfg, db: Database, store: Store, args) -> int:
    raws = _candidates_from_text(args.json if args.json is not None else sys.stdin.read())
    summary = add_manual_candidates(db, cfg, raws)
    pseudo, signals = build_pseudonyms(db), store.signal_descriptions()
    count = len(summary.candidate_ids)
    print(f"Lauf {summary.run_id} (manuell): {count} {'Kandidat' if count == 1 else 'Kandidaten'}")
    for candidate_id in summary.candidate_ids:
        candidate = store.get_candidate(candidate_id)
        room, when, action = describe(candidate, cfg, pseudo, signals)
        print(f"Kandidat {candidate_id}: {candidate.status}")
        print(f"  {room}  {when}  ->  {action}")
        if candidate.note:
            print(f"  {candidate.note}")
        for name, value in store.results(candidate_id).items():
            print(f"  {name}: {value:g}")
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
            if args.command == "signals":
                return _cmd_signals(db, store)
            if args.command == "scan":
                return _cmd_scan(cfg, db, store, args)
            if args.command == "presence":
                return _cmd_presence(cfg, db, args)
            if args.command == "compare":
                return _cmd_compare(store, args)
            if args.command == "list":
                return _cmd_list(cfg, db, store, args)
            if args.command == "add":
                return _cmd_add(cfg, db, store, args)
            return _cmd_backtest(cfg, db, args)
        finally:
            db.close()
    except (AnalysisConfigError, AnalysisError, LlmError, FileNotFoundError, KeyError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
