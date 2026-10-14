from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from .backfill import CorruptDatabase, UnsupportedSchema, backfill_with_map, load_history_map
from .config import Config, ConfigError, load_config
from .db import Database
from .hass_ws import HassAuthError, HassClient, HassConnectionError
from .live import Collector
from .models import parse_utc
from .registry import LabelEmpty, LabelNotFound, fetch_labeled_entities
from .verify import verify_logged_events


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collector", description="KI-Datenbasis für Home Assistant")
    parser.add_argument("--config", default=os.environ.get("COLLECTOR_CONFIG", "/config/collector.yaml"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Collector starten")
    backfill = sub.add_parser("backfill", help="alte home-assistant_v2.db einspielen (nur lesend)")
    backfill.add_argument("--db", required=True)
    backfill.add_argument("--from", dest="since")
    backfill.add_argument("--to", dest="until")
    backfill.add_argument("--history-map")
    backfill.add_argument("--skip-integrity-check", action="store_true")
    verify = sub.add_parser("verify", help="manual_actions mit thermostat_actor_logged vergleichen")
    verify.add_argument("--db", required=True)
    verify.add_argument("--min-match-rate", type=float, default=0.95)
    purge = sub.add_parser("purge-entity", help="alle Daten einer Entität endgültig löschen")
    purge.add_argument("entity_id")
    purge.add_argument("--yes", action="store_true")
    return parser


def _token() -> str:
    token = os.environ.get("HA_TOKEN")
    if not token:
        raise ConfigError("Umgebungsvariable HA_TOKEN fehlt (Long-Lived Access Token von Home Assistant)")
    return token


async def _run(cfg: Config) -> None:
    token = _token()
    db = Database(cfg.database)
    collector = Collector(
        cfg, db, lambda: HassClient(cfg.ha_url, token),
        health_file=Path(os.environ.get("HEALTH_FILE", "/tmp/collector.health")),
    )
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, collector.stop)
        except NotImplementedError:      # Windows
            pass
    try:
        await collector.run()
    finally:
        db.close()


async def _labeled(cfg: Config):
    client = HassClient(cfg.ha_url, _token())
    await client.connect()
    try:
        return await fetch_labeled_entities(client, cfg.label)
    finally:
        await client.close()


def _cmd_backfill(cfg: Config, args: argparse.Namespace) -> int:
    infos = asyncio.run(_labeled(cfg))
    entries = load_history_map(args.history_map) if args.history_map else []
    db = Database(cfg.database)
    try:
        result = backfill_with_map(
            cfg, db, infos, entries, args.db,
            since=parse_utc(args.since) if args.since else None,
            until=parse_utc(args.until) if args.until else None,
            check_integrity=not args.skip_integrity_check,
        )
    finally:
        db.close()
    print(f"Gelesen: {result.rows_read}, neu gespeichert: {result.rows_inserted}, "
          f"Kontext ergänzt: {result.rows_enriched}, Zeitraum: {result.first_ts} bis {result.last_ts}")
    return 0


def _cmd_verify(cfg: Config, args: argparse.Namespace) -> int:
    db = Database(cfg.database)
    try:
        report = verify_logged_events(args.db, db)
    finally:
        db.close()
    print(f"Protokollierte manuelle Eingriffe: {report.logged_manual}")
    print(f"  davon zugeordnet: {report.matched}")
    print(f"  Entität nicht aufgezeichnet: {report.untracked_entity}")
    print(f"  nicht zugeordnet: {len(report.unmatched)}")
    print(f"Manuelle Eingriffe ohne Logger-Ereignis: {report.extra_manual}")
    print(f"Trefferquote: {report.match_rate:.1%} (Soll: mindestens {args.min_match_rate:.0%})")
    for item in report.unmatched[:20]:
        print(f"  fehlt: {item}")
    return 0 if report.match_rate >= args.min_match_rate else 1


def _cmd_purge(cfg: Config, args: argparse.Namespace) -> int:
    db = Database(cfg.database)
    try:
        rows = db.count_changes(args.entity_id)
        print(f"{rows} Zeilen für {args.entity_id} werden endgültig gelöscht.")
        if not args.yes and input("Wirklich löschen? [ja/NEIN] ").strip().lower() != "ja":
            print("Abgebrochen.")
            return 1
        db.purge_entity(args.entity_id)
        print("Gelöscht.")
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.command == "run":
            _token()
            asyncio.run(_run(cfg))
            return 0
        if args.command == "backfill":
            return _cmd_backfill(cfg, args)
        if args.command == "verify":
            return _cmd_verify(cfg, args)
        return _cmd_purge(cfg, args)
    except (ConfigError, FileNotFoundError, LabelNotFound, LabelEmpty, HassAuthError, HassConnectionError,
            UnsupportedSchema, CorruptDatabase) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
