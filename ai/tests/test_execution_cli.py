from pathlib import Path

import pytest

from collector.db import Database
from execution.cli import main
from execution.models import Deployment, RuleEvent
from execution.store import ExecStore

ROOT = Path(__file__).resolve().parent.parent
CONFIG = str(ROOT / "config" / "execution.yaml")


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("HA_URL", "ws://ha.test:8123/api/websocket")
    monkeypatch.delenv("HA_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("MQTT_HOST", raising=False)
    return str(tmp_path / "x.sqlite")


def run(path, *args):
    return main(["--config", CONFIG, "--analysis-config", str(ROOT / "config" / "analysis.yaml"), "--db", path, *args])


def test_rules_without_any_rule(env, capsys):
    assert run(env, "rules") == 0
    assert "Keine Regeln angelegt." in capsys.readouterr().out


def test_rules_lists_state_and_numbers(env, capsys):
    db = Database(env)
    store = ExecStore(db)
    store.add_deployment(Deployment("r5", 5, "bad", "automation.x", "homeai_rule_r5", "climate.bt", "aktiv", "1.0",
                                    "Bad: Mo-Fr 06:30 -> Preset comfort", "2026-10-15T00:00:00.000000+00:00"))
    store.add_event(RuleEvent("r5", "2026-10-15T04:30:00.000000+00:00", "aktiv", "ausgefuehrt", "", {}, {}, "{}"))
    db.close()
    assert run(env, "rules") == 0
    out = capsys.readouterr().out
    assert "r5" in out and "aktiv" in out and "Mo-Fr 06:30" in out and "Auslösungen 1" in out


def test_init_needs_the_write_token(env, capsys):
    assert run(env, "init") == 2
    assert "HA_WRITE_TOKEN" in capsys.readouterr().err


def test_deploy_needs_the_broker(env, monkeypatch, capsys):
    monkeypatch.setenv("HA_WRITE_TOKEN", "secret")
    assert run(env, "deploy", "1") == 2
    assert "MQTT_HOST" in capsys.readouterr().err


def test_serve_needs_the_broker(env, monkeypatch, capsys):
    monkeypatch.setenv("HA_WRITE_TOKEN", "secret")
    assert run(env, "serve") == 2
    assert "MQTT_HOST" in capsys.readouterr().err


def test_unknown_command_is_rejected_by_the_parser(env):
    with pytest.raises(SystemExit) as exit_info:
        run(env, "nonsense")
    assert exit_info.value.code == 2
