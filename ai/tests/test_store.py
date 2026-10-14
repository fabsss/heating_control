from datetime import datetime, timezone

from analysis.models import STATUS_OPEN, STATUS_PASSED, Candidate

NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def candidate(**overrides) -> Candidate:
    base = dict(
        room="r_living",
        trigger={"type": "time", "weekdays": [0, 1, 2, 3, 4], "time": "17:30"},
        action={"type": "setpoint", "value": 21.0},
        conditions=[{"type": "outside_temp", "op": "below", "value": 8}],
        hypothesis="Feierabend",
    )
    base.update(overrides)
    return Candidate(**base)


def test_run_lifecycle(store):
    run_id = store.start_run("weekly", "m", {"von": "a"}, NOW)
    assert store.get_run(run_id)["status"] == "laeuft"
    store.finish_run(run_id, "abgeschlossen", NOW, prompt_hash="h", prompt_text="p",
                     response_text="r", tokens_in=10, tokens_out=5, cost_usd=0.01)
    row = store.get_run(run_id)
    assert (row["status"], row["tokens_in"], row["cost_usd"]) == ("abgeschlossen", 10, 0.01)
    assert row["finished_at"] == "2026-01-01T10:00:00.000000+00:00"
    assert [r["id"] for r in store.list_runs()] == [run_id]


def test_candidate_roundtrip_and_status_update(store):
    run_id = store.start_run("clusters", "m", {}, NOW)
    cid = store.add_candidate(run_id, candidate())
    store.commit()
    loaded = store.get_candidate(cid)
    assert loaded.id == cid and loaded.run_id == run_id and loaded.status == STATUS_OPEN
    assert loaded.trigger["time"] == "17:30" and loaded.conditions[0]["value"] == 8
    store.set_candidate_status(cid, STATUS_PASSED, "ok")
    store.commit()
    assert store.get_candidate(cid).status == STATUS_PASSED
    assert store.get_candidate(cid).note == "ok"


def test_results_are_replaced_not_appended(store):
    run_id = store.start_run("weekly", "m", {}, NOW)
    cid = store.add_candidate(run_id, candidate())
    store.save_results(cid, {"abdeckung": 0.5, "faelle": 3.0})
    store.save_results(cid, {"abdeckung": 0.9})
    store.commit()
    assert store.results(cid) == {"abdeckung": 0.9}


def test_list_candidates_filters(store):
    first = store.start_run("weekly", "m", {}, NOW)
    second = store.start_run("clusters", "m", {}, NOW)
    store.add_candidate(first, candidate())
    store.add_candidate(second, candidate(status=STATUS_PASSED))
    store.commit()
    assert len(store.list_candidates()) == 2
    assert len(store.list_candidates(run_id=first)) == 1
    assert [c.status for c in store.list_candidates(status=STATUS_PASSED)] == [STATUS_PASSED]


def test_rollback_discards_uncommitted_candidates_but_keeps_run(store):
    run_id = store.start_run("weekly", "m", {}, NOW)
    store.add_candidate(run_id, candidate())
    store.rollback()
    assert store.list_candidates() == []
    assert store.get_run(run_id) is not None


def test_pseudonyms_roundtrip(store):
    store.save_pseudonyms({"person_1": ("person.a", "person"), "person_2": ("person.b", "person")})
    store.commit()
    assert store.load_pseudonyms() == {"person_1": "person.a", "person_2": "person.b"}
