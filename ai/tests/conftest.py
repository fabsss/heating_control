from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from analysis.config import load_analysis_config
from analysis.store import Store
from collector.config import load_config
from collector.db import Database
from tests.fake_hass import FakeHass

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load_config(
        ROOT / "config" / "collector.yaml",
        env={"HA_URL": "ws://ha.test/api/websocket", "DB_PATH": ":memory:"},
    )


@pytest.fixture
def acfg():
    return load_analysis_config(ROOT / "config" / "analysis.yaml")


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def store(db):
    return Store(db)


@pytest_asyncio.fixture
async def fake_hass():
    fake = FakeHass()
    app = web.Application()
    app.router.add_get("/api/websocket", fake.handler)
    server = TestServer(app)
    await server.start_server()
    fake.url = f"ws://127.0.0.1:{server.port}/api/websocket"
    try:
        yield fake
    finally:
        await server.close()
