import json
from dataclasses import replace

import pytest

from analysis.backtest import Backtester
from analysis.models import STATUS_PASSED, STATUS_UNTESTABLE, Candidate
from collector.models import utcnow
from analysis.llm import InvalidResponse
from analysis.runner import (NothingToAnalyze, TooFewInterventions, backtest_candidate, prepare,
                             run_analysis)
from analysis.store import Store
from tests.analysis_fixture import ROOM, build_habit_db

HABIT_TIME = {"room": "Wohnzimmer", "hypothesis": "Feierabend",
              "trigger": {"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"},
              "action": {"type": "setpoint", "value": 21}}
HABIT_EVENT = {"room": "Wohnzimmer", "hypothesis": "person_2 kommt heim",
               "trigger": {"type": "event", "event": "person_arrives", "person": "person_2"},
               "action": {"type": "setpoint", "value": 21}}
NOT_ENOUGH = {"room": "Wohnzimmer", "hypothesis": "Morgens",
              "trigger": {"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "09:00"},
              "action": {"type": "setpoint", "value": 21}}
UNKNOWN_TRIGGER = {"room": "Wohnzimmer", "trigger": {"type": "magic"}, "action": {"type": "setpoint", "value": 21}}
UNKNOWN_ROOM = {"room": "Keller", "trigger": HABIT_TIME["trigger"], "action": HABIT_TIME["action"]}


def answer(*items, **extra):
    return {"content": json.dumps({"candidates": list(items)}, ensure_ascii=False), **extra}


@pytest.fixture
def cfg(db, acfg, fake_llm):
    build_habit_db(db)
    return replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=(),
                   openrouter_url=fake_llm.url, retry_backoff_s=0.0)


async def run(db, cfg, mode="weekly", **kwargs):
    return await run_analysis(db, cfg, mode, api_key="geheim", **kwargs)


async def test_known_habit_is_found_across_dst_change(db, cfg, fake_llm):
    fake_llm.responses = [answer(HABIT_TIME, HABIT_EVENT, NOT_ENOUGH, UNKNOWN_TRIGGER, UNKNOWN_ROOM)]
    summary = await run(db, cfg)
    assert summary.candidates == 5
    assert summary.by_status == {"bestanden": 2, "zu_wenig_belege": 1, "nicht_pruefbar": 2}
    store = Store(db)
    by_hypothesis = {c.hypothesis: c for c in store.list_candidates(run_id=summary.run_id)}
    time_rule = by_hypothesis["Feierabend"]
    assert time_rule.status == STATUS_PASSED and time_rule.room == ROOM
    metrics = store.results(time_rule.id)
    assert metrics["faelle"] == 30.0 and metrics["wochen"] == 6.0
    assert metrics["abdeckung"] == 1.0 and metrics["fehlausloesungen"] == 0.0
    event_rule = by_hypothesis["person_2 kommt heim"]
    assert event_rule.status == STATUS_PASSED and event_rule.trigger["person"] == "person.svenja"
    assert by_hypothesis["Morgens"].status == "zu_wenig_belege"
    untestable = [c for c in store.list_candidates() if c.status == STATUS_UNTESTABLE]
    assert len(untestable) == 2 and all(c.note for c in untestable)


async def test_both_modes_give_the_same_backtest_for_the_same_answer(db, cfg, fake_llm):
    fake_llm.responses = [answer(HABIT_TIME, NOT_ENOUGH), answer(HABIT_TIME, NOT_ENOUGH)]
    first = await run(db, cfg, "weekly")
    second = await run(db, cfg, "clusters")
    store = Store(db)
    pairs = [(c.status, store.results(c.id)) for c in store.list_candidates(run_id=first.run_id)]
    again = [(c.status, store.results(c.id)) for c in store.list_candidates(run_id=second.run_id)]
    assert pairs == again and len(pairs) == 2
    modes = [row["mode"] for row in store.list_runs()]
    assert modes == ["weekly", "clusters"]


async def test_prompt_contains_no_real_names(db, cfg, fake_llm):
    fake_llm.responses = [answer(HABIT_TIME)]
    await run(db, cfg, "clusters")
    sent = "\n".join(m["content"] for m in fake_llm.requests[0]["payload"]["messages"]).lower()
    for forbidden in ("fabian", "svenja", "u-fabian", "u-svenja", "person.fabian", "person.svenja"):
        assert forbidden not in sent
    assert "person_2" in sent and "wohnzimmer" in sent
    stored = Store(db).get_run(1)["prompt_text"].lower()
    assert "svenja" not in stored and "fabian" not in stored


async def test_run_records_tokens_cost_prompt_and_pseudonyms(db, cfg, fake_llm):
    fake_llm.responses = [answer(HABIT_TIME, tokens_in=1234, tokens_out=321, cost=0.5)]
    summary = await run(db, cfg)
    store = Store(db)
    row = store.get_run(summary.run_id)
    assert (row["status"], row["tokens_in"], row["tokens_out"], row["cost_usd"]) == ("abgeschlossen", 1234, 321, 0.5)
    assert row["model"] == cfg.model and len(row["prompt_hash"]) == 16
    assert "Wohnzimmer" in row["prompt_text"] and "Feierabend" in row["response_text"]
    assert json.loads(row["params"])["eingriffe"] == 32
    assert store.load_pseudonyms() == {"person_1": "person.fabian", "person_2": "person.svenja"}
    assert (summary.tokens_in, summary.cost_usd) == (1234, 0.5)


def test_prepare_makes_no_request_and_returns_messages(db, cfg, fake_llm):
    prepared = prepare(db, cfg, "weekly")
    assert fake_llm.requests == []
    assert [m["role"] for m in prepared.messages] == ["system", "user"]
    assert prepared.parse_ctx.rooms == {"Wohnzimmer": ROOM}
    assert prepared.parse_ctx.presets == frozenset({"eco", "comfort", "none"})
    assert len(prepared.env.interventions) == 32


async def test_too_few_interventions_stops_before_the_call(db, acfg, fake_llm):
    build_habit_db(db, weeks=1, with_noise=False)
    cfg = replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=(), openrouter_url=fake_llm.url)
    with pytest.raises(TooFewInterventions, match="5"):
        await run(db, cfg)
    assert fake_llm.requests == [] and Store(db).list_runs() == []


def test_empty_database_and_empty_clusters(db, acfg):
    with pytest.raises(NothingToAnalyze):
        prepare(db, acfg, "weekly")
    build_habit_db(db)
    strict = replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=(),
                     clusters=replace(acfg.clusters, min_size=999))
    with pytest.raises(NothingToAnalyze, match="Cluster"):
        prepare(db, strict, "clusters")


def test_unknown_mode_is_rejected(db, cfg):
    with pytest.raises(ValueError):
        prepare(db, cfg, "wild")


async def test_failed_run_leaves_no_candidates(db, cfg, fake_llm):
    fake_llm.responses = [{"content": "kein json"}, {"content": "immer noch nicht"}]
    with pytest.raises(InvalidResponse):
        await run(db, cfg)
    store = Store(db)
    assert store.list_candidates() == []
    (row,) = store.list_runs()
    assert row["status"] == "fehlgeschlagen" and "JSON" in row["error"]
    assert row["prompt_text"]


async def test_run_is_atomic_when_the_backtest_crashes(db, cfg, fake_llm, monkeypatch):
    fake_llm.responses = [answer(HABIT_TIME, HABIT_EVENT)]
    original = Backtester.evaluate
    calls = {"count": 0}

    def flaky(self, candidate):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("kaputt")
        return original(self, candidate)

    monkeypatch.setattr(Backtester, "evaluate", flaky)
    with pytest.raises(RuntimeError):
        await run(db, cfg)
    store = Store(db)
    assert store.list_candidates() == []
    assert store.list_runs()[0]["status"] == "fehlgeschlagen"


async def test_backtest_candidate_recomputes_and_updates(db, cfg, fake_llm):
    fake_llm.responses = [answer(NOT_ENOUGH)]
    summary = await run(db, cfg)
    store = Store(db)
    (candidate,) = store.list_candidates(run_id=summary.run_id)
    store.set_candidate_status(candidate.id, "abgelehnt", "manuell verstellt")
    store.save_results(candidate.id, {})
    store.commit()
    result = backtest_candidate(db, cfg, candidate.id)
    assert result.status == "zu_wenig_belege"
    assert store.get_candidate(candidate.id).status == "zu_wenig_belege"
    assert store.results(candidate.id)["ausloesungen"] == 30.0


async def test_backtest_keeps_garbage_candidates_untestable(db, cfg):
    store = Store(db)
    run_id = store.start_run("weekly", "m", {}, utcnow())
    garbage = Candidate(room="Garage", trigger={"type": "time", "weekdays": [1], "time": "25:99"},
                        action={"type": "setpoint", "value": 99}, status=STATUS_UNTESTABLE,
                        note="unbekannter Raum")
    empty = Candidate(room="Garage", trigger={}, action={}, status=STATUS_UNTESTABLE, note="unbekannt")
    ids = [store.add_candidate(run_id, item) for item in (garbage, empty)]
    store.commit()
    for candidate_id in ids:
        result = backtest_candidate(db, cfg, candidate_id)
        assert result.status == STATUS_UNTESTABLE
        assert store.get_candidate(candidate_id).status == STATUS_UNTESTABLE
    assert store.get_candidate(ids[0]).note == "unbekannter Raum"


async def test_failed_run_keeps_cost_tokens_and_last_response(db, cfg, fake_llm):
    fake_llm.responses = [{"content": "kein json", "cost": 0.4}, {"content": "immer noch nicht", "cost": 0.6}]
    with pytest.raises(InvalidResponse):
        await run(db, cfg)
    (row,) = Store(db).list_runs()
    assert row["status"] == "fehlgeschlagen"
    assert row["cost_usd"] == pytest.approx(1.0)
    assert row["tokens_in"] and row["response_text"] == "immer noch nicht"
