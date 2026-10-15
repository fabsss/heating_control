import json
from dataclasses import replace

import pytest

from analysis.runner import AnalysisError, add_manual_candidates
from analysis.store import Store
from tests.analysis_fixture import ROOM, build_habit_db
from tests.test_runner import HABIT_EVENT, HABIT_TIME, NOT_ENOUGH, UNKNOWN_ROOM


@pytest.fixture
def cfg(db, acfg, fake_llm):
    build_habit_db(db)
    return replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=(),
                   openrouter_url=fake_llm.url, retry_backoff_s=0.0)


def test_manual_candidates_are_backtested_like_those_of_the_model_and_cost_nothing(db, cfg, fake_llm):
    summary = add_manual_candidates(db, cfg, [HABIT_TIME, NOT_ENOUGH, UNKNOWN_ROOM])
    store = Store(db)
    run = store.get_run(summary.run_id)
    assert (run["mode"], run["model"], run["status"]) == ("manuell", "manuell", "abgeschlossen")
    assert summary.by_status == {"bestanden": 1, "zu_wenig_belege": 1, "nicht_pruefbar": 1}
    stored = store.list_candidates(run_id=summary.run_id)
    assert [c.id for c in stored] == summary.candidate_ids
    assert store.results(stored[0].id)["faelle"] == 30.0 and store.results(stored[0].id)["abdeckung"] == 1.0
    assert "Keller" in stored[2].note
    assert fake_llm.requests == []


def test_a_condition_is_stored_and_filters_the_triggers(db, cfg):
    everything = {"type": "time_window", "from": "00:00", "to": "23:59"}
    never = {"type": "time_window", "from": "03:00", "to": "03:30"}
    summary = add_manual_candidates(db, cfg, [HABIT_EVENT, {**HABIT_EVENT, "conditions": [everything]},
                                              {**HABIT_EVENT, "conditions": [never]}])
    store = Store(db)
    plain, wide, narrow = store.list_candidates(run_id=summary.run_id)
    assert wide.conditions == [everything] and narrow.conditions == [never]
    assert store.results(wide.id) == store.results(plain.id)
    assert store.results(narrow.id)["ausloesungen"] < store.results(plain.id)["ausloesungen"]


def test_more_than_two_conditions_are_not_checkable(db, cfg):
    window = {"type": "time_window", "from": "16:00", "to": "20:00"}
    summary = add_manual_candidates(db, cfg, [{**HABIT_EVENT, "conditions": [window, window, window]}])
    only = Store(db).list_candidates(run_id=summary.run_id)[0]
    assert only.status == "nicht_pruefbar" and "höchstens 2" in only.note


def test_nothing_to_test_is_an_error_and_leaves_no_run(db, cfg):
    with pytest.raises(AnalysisError):
        add_manual_candidates(db, cfg, [])
    assert Store(db).list_runs() == []


def test_the_candidate_can_be_recomputed_later(db, cfg):
    from analysis.runner import backtest_candidate
    summary = add_manual_candidates(db, cfg, [HABIT_TIME])
    outcome = backtest_candidate(db, cfg, summary.candidate_ids[0])
    assert outcome.status == "bestanden"
    assert json.loads(json.dumps(outcome.metrics))["faelle"] == 30.0
