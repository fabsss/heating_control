from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from analysis.config import AnalysisConfigError, load_analysis_config
from analysis.store import Store as AnalysisStore
from collector.db import Database
from collector.hass_ws import HassAuthError, HassConnectionError

from .actions import LiveActions
from .config import ExecutionConfigError, load_execution_config
from .deploy import DeployError, Deployer
from .ha_api import HaError, ha_connection
from .jobs import SubprocessExecutor
from .mqtt import AioMqttBus
from .outcomes import rule_stats
from .serve import serve
from .store import ExecStore
from .translate import TranslateError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="execution", description="Freigabe und Ausführung der KI-Regeln")
    parser.add_argument("--config", default=os.environ.get("EXECUTION_CONFIG", "/app/config/execution.yaml"))
    parser.add_argument("--analysis-config", default=os.environ.get("ANALYSIS_CONFIG", "/app/config/analysis.yaml"))
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "/data/home_ai.sqlite"))
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Sammelschalter, Helfer und Hilfsautomationen anlegen")
    init.add_argument("--room", help="nur dieser Raum (Schlüssel aus execution.yaml)")
    deploy = sub.add_parser("deploy", help="einen bestandenen Kandidaten im Schattenmodus anlegen")
    deploy.add_argument("candidate_id", type=int)
    undeploy = sub.add_parser("undeploy", help="eine Regel entfernen (die Historie bleibt)")
    undeploy.add_argument("rule_id")
    sub.add_parser("rules", help="angelegte Regeln mit Kennzahlen anzeigen")
    sub.add_parser("check", help="Blueprint, Helfer, Schalter und Regeln in Home Assistant prüfen")
    sub.add_parser("serve", help="Dauerbetrieb: Meldungen auswerten, Dashboard bedienen")
    return parser


def _cmd_rules(args: argparse.Namespace) -> int:
    cfg = load_execution_config(args.config)
    db = Database(args.db)
    try:
        store = ExecStore(db)
        deployments = store.list_deployments()
        if not deployments:
            print("Keine Regeln angelegt.")
            return 0
        for item in deployments:
            stats = rule_stats(store, item.rule_id, cfg)
            print(f"{item.rule_id}  {item.state:<9} {item.description}")
            print(f"      {stats.text}; Auslösungen {stats.events}; Signal: {stats.reife}")
        return 0
    finally:
        db.close()


def _require_broker(cfg) -> None:
    if cfg.mqtt is None:
        raise ExecutionConfigError("MQTT_HOST fehlt (Umgebungsvariable)")


async def _deployer_command(args: argparse.Namespace) -> int:
    cfg = load_execution_config(args.config)
    token = cfg.require_token()
    _require_broker(cfg)
    acfg = load_analysis_config(args.analysis_config)
    db = Database(args.db)
    try:
        store, astore = ExecStore(db), AnalysisStore(db)
        async with ha_connection(cfg.ha_url, token) as (rest, ws):
            deployer = Deployer(cfg, db, store, astore, acfg, rest, ws, AioMqttBus(cfg.mqtt))
            if args.command == "init":
                for line in await deployer.init(args.room):
                    print(line)
                return 0
            if args.command == "deploy":
                result = await deployer.deploy(args.candidate_id)
                print(f"Regel {result.rule_id} im Schattenmodus angelegt ({result.automation_entity}).")
                for hint in result.hints:
                    print(f"Hinweis: {hint}")
                return 0
            if args.command == "undeploy":
                await deployer.undeploy(args.rule_id)
                print(f"Regel {args.rule_id} entfernt.")
                return 0
            lines = await deployer.check()
            for line in lines:
                print(line)
            return 1 if any(line.startswith("FEHLT") for line in lines) else 0
    finally:
        db.close()


async def _serve(args: argparse.Namespace) -> int:
    cfg = load_execution_config(args.config)
    cfg.require_token()
    _require_broker(cfg)
    acfg = load_analysis_config(args.analysis_config)
    db = Database(args.db)
    bus = AioMqttBus(cfg.mqtt)
    actions = LiveActions(cfg, db, ExecStore(db), AnalysisStore(db), acfg, bus)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:          # Windows
            pass
    try:
        await serve(cfg, acfg, db, bus, actions, SubprocessExecutor(), stop)
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "rules":
            return _cmd_rules(args)
        if args.command == "serve":
            return asyncio.run(_serve(args))
        return asyncio.run(_deployer_command(args))
    except (ExecutionConfigError, DeployError, HaError, TranslateError, AnalysisConfigError, FileNotFoundError,
            HassAuthError, HassConnectionError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
