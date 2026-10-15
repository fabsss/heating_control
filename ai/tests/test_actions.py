from dataclasses import replace
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from analysis.store import Store as AnalysisStore
from execution.actions import LiveActions
from execution.mqtt import InMemoryBus
from execution.store import ExecStore
from tests.execution_fixture import seed_database, seed_home_assistant
from tests.fake_ha_rest import FakeHaRest
from tests.fake_hass import FakeHass
from tests.ha_template import utc


@pytest_asyncio.fixture
async def ha():
    hass, rest = FakeHass(), FakeHaRest()
    app = web.Application()
    app.router.add_get("/api/websocket", hass.handler)
    rest.add_routes(app)
    server = TestServer(app)
    await server.start_server()
    hass.url = f"ws://127.0.0.1:{server.port}/api/websocket"
    rest.base = f"http://127.0.0.1:{server.port}"
    try:
        yield SimpleNamespace(hass=hass, rest=rest)
    finally:
        await server.close()


async def test_live_actions_talk_to_home_assistant(db, acfg, ecfg, ha):
    astore, store = AnalysisStore(db), ExecStore(db)
    candidate_id = seed_database(db, astore)
    helpers, dashboards = seed_home_assistant(ha.rest, ha.hass)
    actions = LiveActions(replace(ecfg, ha_url=ha.hass.url), db, store, astore, acfg, InMemoryBus(),
                          now=lambda: utc(2026, 10, 15, 12, 0))
    text = await actions.deploy(candidate_id)
    assert "r1" in text and "Schattenmodus" in text and "homeai_rule_r1" in ha.rest.automations
    assert "Regeln: 1 angelegt" in await actions.status()
    assert "Blueprint fabsss/ki_regel.yaml" in await actions.check()
    await actions.refresh_dashboard()
    assert dashboards.saved
    assert "entfernt" in await actions.undeploy("r1")
    assert "homeai_rule_r1" not in ha.rest.automations


async def test_errors_of_the_deployer_reach_the_caller(db, acfg, ecfg, ha):
    astore, store = AnalysisStore(db), ExecStore(db)
    seed_home_assistant(ha.rest, ha.hass)
    actions = LiveActions(replace(ecfg, ha_url=ha.hass.url), db, store, astore, acfg, InMemoryBus())
    with pytest.raises(Exception, match="existiert nicht"):
        await actions.deploy(42)
