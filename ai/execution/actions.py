from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Callable

from analysis.config import AnalysisConfig
from analysis.store import Store as AnalysisStore
from collector.db import Database
from collector.models import utcnow

from .config import ExecutionConfig
from .deploy import Deployer
from .ha_api import ha_connection
from .jobs import status_text
from .store import ExecStore


class LiveActions:
    """Aktionen des Dashboards gegen die echte Home Assistant; je Aktion eine eigene Verbindung."""

    def __init__(self, cfg: ExecutionConfig, db: Database, store: ExecStore, astore: AnalysisStore,
                 acfg: AnalysisConfig, bus, now: Callable[[], datetime] = utcnow):
        self.cfg, self.db, self.store, self.astore, self.acfg, self.bus, self.now = cfg, db, store, astore, acfg, bus, now

    @asynccontextmanager
    async def _deployer(self):
        async with ha_connection(self.cfg.ha_url, self.cfg.require_token()) as (rest, ws):
            yield Deployer(self.cfg, self.db, self.store, self.astore, self.acfg, rest, ws, self.bus, self.now)

    async def status(self) -> str:
        return status_text(self.db, self.store)

    async def deploy(self, candidate_id: int) -> str:
        async with self._deployer() as deployer:
            result = await deployer.deploy(candidate_id)
        lines = [f"Regel {result.rule_id} im Schattenmodus angelegt ({result.automation_entity})."]
        return "\n".join(lines + [f"Hinweis: {hint}" for hint in result.hints])

    async def undeploy(self, rule_id: str) -> str:
        async with self._deployer() as deployer:
            await deployer.undeploy(rule_id)
        return f"Regel {rule_id} entfernt."

    async def check(self) -> str:
        async with self._deployer() as deployer:
            return "\n".join(await deployer.check())

    async def refresh_dashboard(self) -> None:
        async with self._deployer() as deployer:
            await deployer.refresh_dashboard()
