from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable

from analysis.config import AnalysisConfig
from analysis.store import Store as AnalysisStore
from collector.db import Database
from collector.models import utcnow

from .config import ExecutionConfig
from .controls import publish_controls
from .jobs import JobActions, JobRunner, candidate_options, rule_options
from .mqtt import publish_rule_entities, rule_label
from .service import Service
from .store import ExecStore

log = logging.getLogger("execution")


async def serve(cfg: ExecutionConfig, acfg: AnalysisConfig, db: Database, bus, actions: JobActions, executor,
                stop: asyncio.Event, *, interval: float = 60.0, now: Callable[[], datetime] = utcnow) -> None:
    store, astore = ExecStore(db), AnalysisStore(db)

    def candidates() -> list[str]:
        return candidate_options(db, astore, acfg, store)

    def rules() -> list[str]:
        return rule_options(store)

    async def refresh() -> None:
        await publish_controls(bus, candidates(), rules())

    service = Service(cfg, db, store, bus, now)
    runner = JobRunner(cfg, store, bus, executor, actions, candidates=candidates, rules=rules, refresh=refresh, now=now)
    service.register()
    runner.register()

    async def announce() -> None:
        await refresh()
        await runner.start()
        for deployment in store.list_deployments():
            await publish_rule_entities(bus, deployment.rule_id, rule_label(deployment.rule_id, deployment.description))
        await service.publish_all_stats()
        try:
            await actions.refresh_dashboard()
        except Exception:
            log.exception("Das Dashboard konnte nicht aktualisiert werden")

    bus.on_connect(announce)

    async def ticker() -> None:
        while not stop.is_set():
            await runner.tick()
            try:
                await asyncio.wait_for(stop.wait(), 1.0)
            except asyncio.TimeoutError:
                pass

    await asyncio.gather(bus.run(stop), service.run_loop(stop, interval), ticker())
    await runner.wait()
