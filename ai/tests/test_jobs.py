import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from analysis.store import Store as AnalysisStore
from collector.models import Change
from execution.jobs import (FENCE, JobRunner, SubprocessExecutor, candidate_options, rule_options, status_text)
from execution.models import Deployment
from execution.mqtt import InMemoryBus
from execution.store import ExecStore
from tests.execution_fixture import AREA, CLIMATE, seed_database
from tests.ha_template import utc
from tests.job_fakes import Clock, FakeActions, FakeExecutor


@pytest.fixture
def w(db, ecfg):
    store, bus, executor, actions, clock = ExecStore(db), InMemoryBus(), FakeExecutor(), FakeActions(), Clock()
    refreshed = []

    async def refresh():
        refreshed.append(1)

    runner = JobRunner(ecfg, store, bus, executor, actions, candidates=lambda: ["7: Bad", "9: Wohnzimmer"],
                       rules=lambda: ["r7: Bad"], refresh=refresh, now=clock)
    runner.register()
    return SimpleNamespace(runner=runner, store=store, bus=bus, executor=executor, actions=actions, clock=clock,
                           refreshed=refreshed)


async def press(w, name):
    await w.bus.deliver(f"homeai/cmd/{name}", "PRESS")
    await w.runner.wait()


def states(w):
    return [json.loads(m.payload)["state"] for m in w.bus.published if m.topic == "homeai/job/state"]


def result(w):
    return json.loads(w.bus.retained["homeai/job/result"])


async def test_start_publishes_idle_and_the_selection(w):
    await w.runner.start()
    assert json.loads(w.bus.retained["homeai/job/state"])["state"] == "idle"
    assert w.bus.retained["homeai/state/llm_arm"] == "OFF"
    assert w.bus.retained["homeai/sel/mode/state"] == "scan"


async def test_scan_runs_the_cli_and_reports_the_output(w):
    await press(w, "scan")
    assert w.executor.calls == [["scan"]]
    assert states(w) == ["laeuft", "idle"]
    assert result(w)["title"] == "Scan (ohne Modell): ok"
    assert result(w)["markdown"].startswith(FENCE) and "ausgabe" in result(w)["markdown"]
    assert w.store.recent_jobs()[0]["state"] == "ok"


async def test_failed_commands_are_reported_as_errors(w):
    w.executor.result = (2, "Fehler: kaputt")
    await press(w, "signals")
    assert result(w)["title"] == "Signale anzeigen: fehler" and "kaputt" in result(w)["markdown"]
    assert w.store.recent_jobs()[0]["state"] == "fehler"


async def test_unknown_commands_and_payloads_are_ignored(w):
    await w.bus.deliver("homeai/cmd/nonsense", "PRESS")
    await w.bus.deliver("homeai/cmd/scan", "foo")
    await w.runner.wait()
    assert w.executor.calls == [] and "homeai/job/state" not in w.bus.retained


async def test_a_second_job_is_rejected_while_one_runs(w):
    w.executor.gate = asyncio.Event()
    await w.bus.deliver("homeai/cmd/scan", "PRESS")
    await w.bus.deliver("homeai/cmd/signals", "PRESS")
    assert "läuft bereits" in result(w)["markdown"] and result(w)["title"].endswith("abgelehnt")
    for _ in range(3):
        await asyncio.sleep(0)
    assert w.executor.calls == [["scan"]]
    w.executor.gate.set()
    await w.runner.wait()
    assert w.executor.calls == [["scan"]]


async def test_the_model_run_needs_the_cost_release(w):
    await press(w, "llm_run")
    assert w.executor.calls == [] and "Kosten" in result(w)["markdown"]
    await w.bus.deliver("homeai/cmd/llm_arm", "ON")
    assert w.bus.retained["homeai/state/llm_arm"] == "ON"
    await press(w, "llm_run")
    assert w.executor.calls == [["run", "--mode", "scan"]]
    assert w.bus.retained["homeai/state/llm_arm"] == "OFF"          # die Freigabe gilt nur für einen Lauf
    await press(w, "llm_run")
    assert len(w.executor.calls) == 1 and "Kosten" in result(w)["markdown"]


async def test_the_release_expires_by_itself(w):
    await w.bus.deliver("homeai/cmd/llm_arm", "ON")
    w.clock.value += timedelta(seconds=30)
    await w.runner.tick()
    assert w.bus.retained["homeai/state/llm_arm"] == "ON"
    w.clock.value += timedelta(seconds=31)
    await w.runner.tick()
    assert w.bus.retained["homeai/state/llm_arm"] == "OFF"
    await press(w, "llm_run")
    assert w.executor.calls == []


async def test_the_release_can_be_withdrawn(w):
    await w.bus.deliver("homeai/cmd/llm_arm", "ON")
    await w.bus.deliver("homeai/cmd/llm_arm", "OFF")
    await press(w, "llm_run")
    assert w.executor.calls == []


async def test_daily_limit_for_model_runs(w):
    for _ in range(3):
        w.store.start_job("llm_run", {}, w.clock.value)
    await w.bus.deliver("homeai/cmd/llm_arm", "ON")
    await press(w, "llm_run")
    assert w.executor.calls == [] and "Tageslimit" in result(w)["markdown"]


async def test_yesterdays_runs_do_not_count(w):
    for _ in range(3):
        w.store.start_job("llm_run", {}, w.clock.value - timedelta(days=1))
    await w.bus.deliver("homeai/cmd/llm_arm", "ON")
    await press(w, "llm_run")
    assert w.executor.calls == [["run", "--mode", "scan"]]


async def test_mode_selection_is_validated_and_used(w):
    await w.bus.deliver("homeai/sel/mode/set", "weekly")
    assert w.bus.retained["homeai/sel/mode/state"] == "weekly"
    await w.bus.deliver("homeai/sel/mode/set", "nonsense")
    assert w.runner.selection["mode"] == "weekly"
    await press(w, "llm_dry")
    assert w.executor.calls == [["run", "--mode", "weekly", "--dry-run"]]


async def test_deploy_uses_the_selected_candidate_and_refreshes_the_controls(w):
    await w.bus.deliver("homeai/sel/candidate/set", "7: Bad")
    await press(w, "deploy")
    assert w.actions.deployed == [7] and "r7 angelegt" in result(w)["markdown"]
    assert w.refreshed == [1]


async def test_deploy_without_a_selection_is_an_error(w):
    await press(w, "deploy")
    assert w.actions.deployed == [] and result(w)["title"].endswith("fehler") and "Kein Kandidat" in result(w)["markdown"]


async def test_selection_of_unknown_options_is_ignored(w):
    await w.bus.deliver("homeai/sel/candidate/set", "99: Küche")
    await w.bus.deliver("homeai/sel/other/set", "x")
    assert w.runner.selection["candidate"] == "–"


async def test_undeploy_uses_the_selected_rule(w):
    await w.bus.deliver("homeai/sel/rule/set", "r7: Bad")
    await press(w, "undeploy")
    assert w.actions.undeployed == ["r7"] and w.refreshed == [1]


async def test_status_and_check_use_the_actions(w):
    await press(w, "status")
    assert "Status: 2 Regeln" in result(w)["markdown"]
    await press(w, "check")
    assert "OK    alles" in result(w)["markdown"]


async def test_long_output_is_shortened(w):
    w.executor.result = (0, "x" * 20000)
    await press(w, "scan")
    markdown = result(w)["markdown"]
    assert len(markdown) < 12100 and "gekürzt" in markdown


async def test_subprocess_executor_runs_a_module():
    code, output = await SubprocessExecutor(module="platform")([])
    assert code == 0 and output.strip()


def test_candidate_and_rule_options(db, acfg):
    astore, store = AnalysisStore(db), ExecStore(db)
    candidate_id = seed_database(db, astore)
    options = candidate_options(db, astore, acfg, store)
    assert len(options) == 1 and options[0].startswith(f"{candidate_id}: Bad ")
    store.add_deployment(Deployment("r1", candidate_id, AREA, "automation.x", "homeai_rule_r1", CLIMATE, "schatten",
                                    "1.0", "Bad: Test", "2026-10-15T00:00:00.000000+00:00"))
    assert candidate_options(db, astore, acfg, store) == []
    assert rule_options(store) == ["r1: Bad: Test"]


def test_status_text(db):
    astore, store = AnalysisStore(db), ExecStore(db)
    candidate_id = seed_database(db, astore)
    db.insert_changes([Change(utc(2026, 10, 15, 10, 0), "person.fabian", "home")], "live")
    store.add_deployment(Deployment("r1", candidate_id, AREA, "automation.x", "homeai_rule_r1", CLIMATE, "schatten",
                                    "1.0", "Bad: Test", "2026-10-15T00:00:00.000000+00:00"))
    store.finish_job(store.start_job("scan", {}, utc(2026, 10, 15, 11, 0)), "ok", "x", utc(2026, 10, 15, 11, 1))
    text = status_text(db, store)
    assert "Regeln: 1 angelegt, 0 aktiv, 1 im Schatten" in text
    assert "Aufgezeichnete Entitäten: 2" in text
    assert "Letztes aufgezeichnetes Ereignis: 2026-10-15T10:00:00.000000+00:00" in text
    assert "scan: ok" in text
