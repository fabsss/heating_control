from pathlib import Path

import pytest

from collector.config import load_config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load_config(
        ROOT / "config" / "collector.yaml",
        env={"HA_URL": "ws://ha.test/api/websocket", "DB_PATH": ":memory:"},
    )
