import asyncio
import json

import pytest
import yaml

from analysis.candidates import describe, signature, weekday_label
from analysis.cli import main
from analysis.models import STATUS_UNTESTABLE, Candidate
from analysis.pseudonym import Pseudonyms
from analysis.store import Store
from collector.db import Database
from tests.analysis_fixture import ROOM, build_habit_db
from tests.conftest import ROOT
from tests.test_runner import HABIT_EVENT, HABIT_TIME, NOT_ENOUGH, UNKNOWN_ROOM, answer


@pytest.fixture
def files(tmp_path, fake_llm):
    database = Database(tmp_path / "home_ai.sqlite")
    build_habit_db(database)
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


async def test_run_prints_summary_and_stores_candidates(files, fake_llm, capsys):
    fake_llm.responses = [answer(HABIT_TIME, UNKNOWN_ROOM, tokens_in=10, tokens_out=5, cost=0.0123)]
    assert await cli(files, "run", "--mode", "weekly") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Lauf 1 (weekly) abgeschlossen: 2 Kandidaten"
    assert "  bestanden: 1" in out and "  nicht_pruefbar: 1" in out and "  abgelehnt: 0" in out
    assert "Kosten: 0.0123 USD (10 Token ein, 5 aus)" in out
    assert len(stored(files).list_candidates()) == 2


async def test_dry_run_prints_the_prompt_and_calls_nothing(files, fake_llm, capsys):
    assert await cli(files, "run", "--mode", "clusters", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "--- system ---" in out and "--- user ---" in out and "Wohnzimmer" in out
    assert "32 Eingriffe" in out and "kein Aufruf" in out
    assert fake_llm.requests == [] and stored(files).list_runs() == []
    for forbidden in ("fabian", "svenja"):
        assert forbidden not in out.lower()


async def test_run_without_api_key_exits_with_2(files, fake_llm, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert await cli(files, "run", "--mode", "weekly") == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
    assert fake_llm.requests == []


async def test_compare_shows_both_modes_and_their_overlap(files, fake_llm, capsys):
    fake_llm.responses = [answer(HABIT_TIME, NOT_ENOUGH), answer(HABIT_TIME, HABIT_EVENT)]
    assert await cli(files, "run", "--mode", "weekly") == 0
    assert await cli(files, "run", "--mode", "clusters") == 0
    capsys.readouterr()
    assert await cli(files, "compare") == 0
    out = capsys.readouterr().out
    assert "weekly" in out and "clusters" in out and "bestanden" in out
    assert "Überlappung Lauf 1 / Lauf 2: gemeinsam 1, nur Lauf 1: 1, nur Lauf 2: 1" in out


async def test_compare_without_runs_exits_with_1(files, capsys):
    assert await cli(files, "compare") == 1
    assert "Keine" in capsys.readouterr().out


async def test_list_filters_by_status(files, fake_llm, capsys):
    fake_llm.responses = [answer(HABIT_TIME, NOT_ENOUGH, UNKNOWN_ROOM)]
    await cli(files, "run", "--mode", "weekly")
    capsys.readouterr()
    assert await cli(files, "list", "--status", "bestanden") == 0
    out = capsys.readouterr().out
    assert "Wohnzimmer" in out and "Mo–Fr 17:30" in out and "21 °C" in out and "[bestanden]" in out
    assert "abdeckung=1.00" in out and "faelle=30" in out
    assert "zu_wenig_belege" not in out and "nicht_pruefbar" not in out
    assert await cli(files, "list", "--run", "1") == 0
    assert capsys.readouterr().out.count("\n") == 3


async def test_backtest_command_recomputes_one_candidate(files, fake_llm, capsys):
    fake_llm.responses = [answer(HABIT_TIME, NOT_ENOUGH)]
    await cli(files, "run", "--mode", "weekly")
    capsys.readouterr()
    assert await cli(files, "backtest", "2") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Kandidat 2: zu_wenig_belege"
    assert "  ausloesungen: 30.0" in out


async def test_unknown_candidate_exits_with_2(files, capsys):
    assert await cli(files, "backtest", "99") == 2
    assert "99" in capsys.readouterr().err


async def test_too_few_interventions_exits_with_2(tmp_path, fake_llm, capsys):
    database = Database(tmp_path / "wenig.sqlite")
    build_habit_db(database, weeks=1, with_noise=False)
    database.close()
    config = tmp_path / "a.yaml"
    raw = yaml.safe_load((ROOT / "config" / "analysis.yaml").read_text(encoding="utf-8"))
    raw.update(openrouter_url=fake_llm.url, exclusions=[], room_names={ROOM: "Wohnzimmer"})
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    assert await asyncio.to_thread(main, ["--config", str(config), "--db", str(tmp_path / "wenig.sqlite"),
                                          "run", "--mode", "weekly"]) == 2
    assert "mindestens" in capsys.readouterr().err
    assert fake_llm.requests == []


def test_missing_config_and_disabled_zdr_exit_with_2(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "gibt-es-nicht.yaml"), "--db", str(tmp_path / "x.sqlite"),
                 "list"]) == 2
    assert "gibt-es-nicht.yaml" in capsys.readouterr().err
    raw = yaml.safe_load((ROOT / "config" / "analysis.yaml").read_text(encoding="utf-8"))
    raw["provider"]["zdr"] = False
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert main(["--config", str(bad), "--db", str(tmp_path / "x.sqlite"), "list"]) == 2
    assert "zdr" in capsys.readouterr().err


# --- Darstellung und Vergleichsschlüssel -------------------------------------------------

PSEUDO = Pseudonyms(("person.fabian", "person.svenja"), {"person.svenja": "Svenja"}, {})


def candidate(trigger, action=None, conditions=None, status="offen"):
    return Candidate(room=ROOM, trigger=trigger, action=action or {"type": "setpoint", "value": 21.0},
                     conditions=conditions or [], status=status)


def test_weekday_labels():
    assert weekday_label([0, 1, 2, 3, 4]) == "Mo–Fr"
    assert weekday_label([6, 5]) == "Sa–So"
    assert weekday_label(list(range(7))) == "täglich"
    assert weekday_label([0, 2]) == "Mo,Mi"


def test_describe_uses_names_conditions_and_actions(acfg):
    import dataclasses
    cfg = dataclasses.replace(acfg, room_names={ROOM: "Wohnzimmer"})
    trigger = {"type": "event", "event": "person_arrives", "person": "person.svenja"}
    conditions = [{"type": "weekdays", "weekdays": [0, 1, 2, 3, 4]},
                  {"type": "outside_temp", "op": "below", "value": 8.0},
                  {"type": "person_home", "person": "person.fabian", "home": False}]
    room, when, action = describe(candidate(trigger, conditions=conditions), cfg, PSEUDO)
    assert room == "Wohnzimmer"
    assert when == ("Svenja kommt heim wenn Mo–Fr; Außentemperatur unter 8 °C; Fabian ist nicht zu Hause")
    assert action == "21 °C"
    assert describe(candidate({"type": "time", "weekdays": [0], "time": "07:00"},
                              action={"type": "preset", "value": "comfort"}), cfg, PSEUDO)[1:] == (
        "Mo 07:00", "Preset comfort")
    broken = describe(candidate({"type": "kaputt"}), cfg, PSEUDO)
    assert broken[1] == json.dumps({"type": "kaputt"})


def test_signature_ignores_conditions_and_rounds_time_and_setpoint():
    a = candidate({"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"})
    b = candidate({"type": "time", "weekdays": [4, 3, 2, 1, 0], "time": "17:40"},
                  action={"type": "setpoint", "value": 21.2},
                  conditions=[{"type": "outside_temp", "op": "below", "value": 8.0}])
    assert signature(a) == signature(b)
    c = candidate({"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "18:10"})
    assert signature(a) != signature(c)
    d = candidate({"type": "event", "event": "person_arrives", "person": "person.svenja"})
    assert signature(d) != signature(a)
    assert signature(candidate({}, status=STATUS_UNTESTABLE)) is None


async def test_dry_run_uses_openrouter_prices_and_reports_the_source(files, fake_llm, capsys):
    model = yaml.safe_load((ROOT / "config" / "analysis.yaml").read_text(encoding="utf-8"))["model"]
    fake_llm.models = [{"id": model, "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]
    assert await cli(files, "run", "--mode", "weekly", "--dry-run") == 0
    captured = capsys.readouterr()
    assert "Preise: 1.00 / 2.00 USD je Million Token (OpenRouter)" in captured.err
    assert f"Modell: {model} (analysis.yaml)" in captured.err
    assert "kein Aufruf" in captured.out


async def test_env_prices_win_over_openrouter(files, fake_llm, capsys, monkeypatch):
    model = yaml.safe_load((ROOT / "config" / "analysis.yaml").read_text(encoding="utf-8"))["model"]
    fake_llm.models = [{"id": model, "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]
    monkeypatch.setenv("OPENROUTER_PRICE_INPUT", "7")
    monkeypatch.setenv("OPENROUTER_PRICE_OUTPUT", "8")
    assert await cli(files, "run", "--mode", "weekly", "--dry-run") == 0
    assert "Preise: 7.00 / 8.00 USD je Million Token (Umgebungsvariablen)" in capsys.readouterr().err


async def test_unknown_model_falls_back_to_file_prices(files, fake_llm, capsys):
    assert await cli(files, "run", "--mode", "weekly", "--dry-run") == 0
    assert "(analysis.yaml)" in capsys.readouterr().err


async def test_model_from_environment_is_shown_with_its_source(files, fake_llm, capsys, monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "google/gemma-test")
    fake_llm.models = [{"id": "google/gemma-test", "pricing": {"prompt": "0.0000001", "completion": "0.0000002"}}]
    assert await cli(files, "run", "--mode", "weekly", "--dry-run") == 0
    err = capsys.readouterr().err
    assert "Modell: google/gemma-test (Umgebungsvariable OPENROUTER_MODEL)" in err
    assert "Preise: 0.10 / 0.20 USD je Million Token (OpenRouter)" in err
