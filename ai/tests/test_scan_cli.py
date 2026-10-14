import asyncio

import pytest
import yaml

from analysis.cli import main
from analysis.store import Store
from collector.db import Database
from tests.analysis_fixture import ROOM
from tests.conftest import ROOT
from tests.scan_fixture import after_arrival, build_scan_db
from tests.test_scan_mode import ARRIVAL, answer


@pytest.fixture
def files(tmp_path, fake_llm):
    database = Database(tmp_path / "home_ai.sqlite")
    build_scan_db(database, after_arrival(lambda n: [12]))
    database.close()
    raw = yaml.safe_load((ROOT / "config" / "analysis.yaml").read_text(encoding="utf-8"))
    raw.update(openrouter_url=fake_llm.url, retry_backoff_s=0.0, exclusions=[], room_names={ROOM: "Wohnzimmer"})
    config = tmp_path / "analysis.yaml"
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return str(config), str(tmp_path / "home_ai.sqlite")


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "geheim")


async def cli(files, *args) -> int:
    config, database = files
    return await asyncio.to_thread(main, ["--config", config, "--db", database, *args])


def stored(files) -> Store:
    return Store(Database(files[1]))


async def test_signals_command_lists_handles_with_local_entity_ids(files, capsys):
    assert await cli(files, "signals") == 1                                   # noch keine Signale
    assert await cli(files, "scan") == 0
    capsys.readouterr()
    assert await cli(files, "signals") == 0
    out = capsys.readouterr().out
    assert "sig_01" in out and "person.svenja" in out and "categorical" in out and "Anwesenheit person_1" in out


async def test_scan_command_prints_associations_and_stores_a_run(files, capsys):
    assert await cli(files, "scan") == 0
    out = capsys.readouterr().out
    assert "wechselt zu home" in out and "Fenster +0 bis +30 min" in out and "30 von 30" in out and "lift" in out
    (row,) = stored(files).list_runs()
    assert row["mode"] == "scan-only" and row["status"] == "abgeschlossen"
    assert stored(files).list_associations(row["id"])


async def test_scan_command_filters_by_room_and_rejects_unknown_rooms(files, capsys):
    assert await cli(files, "scan", "--room", "Wohnzimmer") == 0
    assert "Wohnzimmer" in capsys.readouterr().out
    assert await cli(files, "scan", "--room", "Keller") == 2


async def test_scan_dry_run_shows_the_prompt_without_names(files, fake_llm, capsys):
    assert await cli(files, "run", "--mode", "scan", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Modus: Scan" in out and "sig_01" in out and "kein Aufruf" in out
    assert fake_llm.requests == [] and "svenja" not in out.lower()


async def test_scan_run_and_list_show_generic_candidates_readably(files, fake_llm, capsys):
    fake_llm.responses = [answer(ARRIVAL)]
    assert await cli(files, "run", "--mode", "scan") == 0
    assert "Lauf 1 (scan) abgeschlossen: 1 Kandidaten" in capsys.readouterr().out
    assert await cli(files, "list") == 0
    out = capsys.readouterr().out
    assert "Anwesenheit person_1 (home oder away) wechselt zu home" in out and "[bestanden]" in out
    assert await cli(files, "compare") == 0
    assert "scan" in capsys.readouterr().out


async def test_compare_prints_the_overlap_for_every_pair_of_modes(files, fake_llm, capsys):
    fake_llm.responses = [answer(ARRIVAL), answer(), answer()]
    for mode in ("scan", "weekly", "clusters"):
        await cli(files, "run", "--mode", mode)
    capsys.readouterr()
    assert await cli(files, "compare") == 0
    out = capsys.readouterr().out
    assert out.count("Überlappung Lauf") == 3
