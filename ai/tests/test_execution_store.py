from datetime import datetime, timedelta, timezone

import pytest

from execution.models import Deployment, RuleEvent
from execution.store import ExecStore

T0 = datetime(2026, 10, 15, 6, 30, tzinfo=timezone.utc)


@pytest.fixture
def store(db):
    return ExecStore(db)


def deployment(rule_id="r5", state="schatten"):
    return Deployment(rule_id, 5, "bad", "automation.ki_regel_r5", "homeai_rule_r5", "climate.bt", state,
                      "1.0", "Mo-Fr 06:30 Komfort", "2026-10-15T06:00:00.000000+00:00")


def event(ts="2026-10-15T06:30:00.000000+00:00", rule_id="r5", reason=""):
    return RuleEvent(rule_id, ts, "schatten", "nicht_ausgefuehrt", reason, {"kind": "preset", "value": "comfort"},
                     {"preset": "eco"}, "{}")


def test_deployment_roundtrip_and_state(store):
    store.add_deployment(deployment())
    assert store.get_deployment("r5").description == "Mo-Fr 06:30 Komfort"
    store.set_deployment_state("r5", "aktiv", T0)
    assert store.get_deployment("r5").state == "aktiv"
    store.set_deployment_state("r5", "entfernt", T0)
    assert store.get_deployment("r5").removed_at is not None
    assert store.list_deployments() == []
    assert [d.rule_id for d in store.list_deployments(include_removed=True)] == ["r5"]
    assert store.get_deployment("zz") is None


def test_events_are_deduplicated_and_pending_until_an_outcome_exists(store):
    assert store.add_event(event()) is True
    assert store.add_event(event()) is False
    (pending,) = store.pending_events()
    assert pending.target == {"kind": "preset", "value": "comfort"} and pending.current_state == {"preset": "eco"}
    store.save_outcome(pending.id, "neutral", T0 - timedelta(hours=1), T0 + timedelta(hours=2))
    assert store.pending_events() == []
    (row,) = store.stats_rows("r5")
    assert (row["outcome"], row["reason"], row["result"]) == ("neutral", "", "nicht_ausgefuehrt")


def test_jobs_are_counted_per_day(store):
    first = store.start_job("llm_run", {"mode": "scan"}, T0)
    store.finish_job(first, "ok", "fertig", T0)
    store.start_job("scan", {}, T0)
    assert store.count_jobs_since("llm_run", T0 - timedelta(hours=1)) == 1
    assert store.count_jobs_since("llm_run", T0 + timedelta(hours=1)) == 0
    assert [job["name"] for job in store.recent_jobs()] == ["scan", "llm_run"]
