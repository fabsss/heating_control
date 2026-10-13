from datetime import datetime, timezone

import pytest

from collector.cli import main
from collector.db import Database
from collector.models import Change, EntityRule
from tests.conftest import ROOT

CONFIG = str(ROOT / "config" / "collector.yaml")


@pytest.fixture
def env(monkeypatch, tmp_path):
    db_path = tmp_path / "home_ai.sqlite"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("HA_URL", "ws://ha.test/api/websocket")
    monkeypatch.delenv("HA_TOKEN", raising=False)
    database = Database(db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    database.upsert_entity(EntityRule("climate.bt", "climate", "heating"), "buero", now)
    database.insert_changes([Change(now, "climate.bt", "heat"), Change(now.replace(hour=1), "climate.bt", "off")], "live")
    database.close()
    return db_path


def count(db_path) -> int:
    database = Database(db_path)
    try:
        return database.count_changes("climate.bt")
    finally:
        database.close()


def test_purge_entity_aborts_without_confirmation(env, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt: "nein")
    assert main(["--config", CONFIG, "purge-entity", "climate.bt"]) == 1
    assert "2 Zeilen" in capsys.readouterr().out
    assert count(env) == 2


def test_purge_entity_deletes_after_confirmation(env, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "ja")
    assert main(["--config", CONFIG, "purge-entity", "climate.bt"]) == 0
    assert count(env) == 0


def test_purge_entity_with_yes_flag_skips_prompt(env, monkeypatch):
    def fail(_prompt):
        raise AssertionError("keine Rückfrage erwartet")

    monkeypatch.setattr("builtins.input", fail)
    assert main(["--config", CONFIG, "purge-entity", "climate.bt", "--yes"]) == 0
    assert count(env) == 0


def test_run_without_token_reports_error(env, capsys):
    assert main(["--config", CONFIG, "run"]) == 2
    assert "HA_TOKEN" in capsys.readouterr().err


def test_missing_config_file_reports_error(env, capsys):
    assert main(["--config", "/gibt/es/nicht.yaml", "purge-entity", "climate.bt"]) == 2
    assert "nicht.yaml" in capsys.readouterr().err
