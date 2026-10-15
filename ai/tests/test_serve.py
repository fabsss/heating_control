import asyncio
import json

from analysis.store import Store as AnalysisStore
from execution.models import Deployment
from execution.mqtt import InMemoryBus, rule_discovery_topics
from execution.serve import serve
from execution.store import ExecStore
from tests.execution_fixture import AREA, CLIMATE, seed_database
from tests.job_fakes import FakeActions, FakeExecutor


async def until(predicate, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("Bedingung wurde nicht erreicht")


async def test_serve_announces_handles_commands_and_stops(db, acfg, ecfg):
    astore, store = AnalysisStore(db), ExecStore(db)
    candidate_id = seed_database(db, astore)
    store.add_deployment(Deployment("r9", 9, AREA, "automation.x", "homeai_rule_r9", CLIMATE, "schatten", "1.0",
                                    "Bad: Test", "2026-10-15T00:00:00.000000+00:00"))
    bus, actions, executor, stop = InMemoryBus(), FakeActions(), FakeExecutor(), asyncio.Event()
    task = asyncio.create_task(serve(ecfg, acfg, db, bus, actions, executor, stop, interval=0.05))
    await until(lambda: "homeai/job/state" in bus.retained and "homeai/rule/r9/stats" in bus.retained)
    options = json.loads(bus.retained["homeassistant/select/homeai/candidate/config"])["options"]
    assert options and options[0].startswith(f"{candidate_id}: Bad ")
    assert set(rule_discovery_topics("r9")) <= set(bus.retained)
    assert json.loads(bus.retained["homeassistant/select/homeai/rule/config"])["options"] == ["r9: Bad: Test"]
    assert actions.refreshed == 1
    await bus.deliver("homeai/cmd/signals", "PRESS")
    await until(lambda: "homeai/job/result" in bus.retained)
    assert executor.calls == [["signals"]]
    stop.set()
    await asyncio.wait_for(task, 3)


async def test_serve_survives_a_failing_dashboard_refresh(db, acfg, ecfg):
    class Broken(FakeActions):
        async def refresh_dashboard(self):
            raise RuntimeError("HA nicht erreichbar")

    bus, stop = InMemoryBus(), asyncio.Event()
    task = asyncio.create_task(serve(ecfg, acfg, db, bus, Broken(), FakeExecutor(), stop, interval=0.05))
    await until(lambda: "homeai/job/state" in bus.retained)
    stop.set()
    await asyncio.wait_for(task, 3)
