import os
import time

from collector.healthcheck import main


def test_fresh_file_is_healthy(tmp_path, monkeypatch):
    health = tmp_path / "h"
    health.touch()
    monkeypatch.setenv("HEALTH_FILE", str(health))
    assert main() == 0


def test_old_file_is_unhealthy(tmp_path, monkeypatch, capsys):
    health = tmp_path / "h"
    health.touch()
    old = time.time() - 700
    os.utime(health, (old, old))
    monkeypatch.setenv("HEALTH_FILE", str(health))
    assert main() == 1
    assert "älter" in capsys.readouterr().out


def test_missing_file_is_unhealthy(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_FILE", str(tmp_path / "gibt-es-nicht"))
    assert main() == 1
