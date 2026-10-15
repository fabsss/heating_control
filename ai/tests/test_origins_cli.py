from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector.cli import main
from collector.db import Database
from collector.models import Change, EntityRule

ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def test_origins_command_rebuilds(tmp_path, monkeypatch, capsys):
    path = tmp_path / "x.sqlite"
    db = Database(path)
    db.upsert_entity(EntityRule("climate.bt", "climate", "heating", attrs=("temperature",)), "buero", T0)
    db.mark_capture_start("calls_since", T0)
    db.insert_changes([Change(T0 + timedelta(seconds=5), "climate.bt", "heat", {"temperature": 21}, None, "u1")], "live")
    db.close()
    monkeypatch.setenv("HA_URL", "ws://ha.test/api/websocket")
    monkeypatch.setenv("DB_PATH", str(path))
    code = main(["--config", str(ROOT / "config" / "collector.yaml"), "origins", "--rebuild"])
    assert code == 0
    assert "Herkunft berechnet für 1 Zeilen" in capsys.readouterr().out
    db = Database(path)
    assert db.conn.execute("SELECT origin FROM change_origin").fetchone()["origin"] == "benutzer"
    db.close()
