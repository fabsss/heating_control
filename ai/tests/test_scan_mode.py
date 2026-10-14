import json
from dataclasses import replace

import pytest

from analysis.candidates import describe, signature
from analysis.llm import InvalidResponse
from analysis.models import STATUS_OPEN, Candidate
from analysis.prompts import build_messages
from analysis.pseudonym import Pseudonyms
from analysis.runner import NothingToAnalyze, backtest_candidate, prepare, run_analysis
from analysis.store import Store
from tests.analysis_fixture import ROOM, local_utc
from tests.scan_fixture import after_arrival, build_scan_db

ARRIVAL = {"room": "Wohnzimmer", "hypothesis": "Heimkehr von der Arbeit",
           "trigger": {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home",
                       "min_prev_min": 180, "offset_min": 0},
           "action": {"type": "setpoint", "value": 21}}
LEGACY = {"room": "Wohnzimmer", "trigger": {"type": "event", "event": "person_arrives", "person": "person_1"},
          "action": {"type": "setpoint", "value": 21}}


def answer(*items, **extra):
    return {"content": json.dumps({"candidates": list(items)}, ensure_ascii=False), **extra}


@pytest.fixture
def cfg(db, acfg, fake_llm):
    build_scan_db(db, after_arrival(lambda n: [12]))
    return replace(acfg, room_names={ROOM: "Wohnzimmer"}, exclusions=(), openrouter_url=fake_llm.url,
                   retry_backoff_s=0.0)


async def run(db, cfg, mode="scan", **kwargs):
    return await run_analysis(db, cfg, mode, api_key="geheim", **kwargs)


# --- Vorbereitung und Datenschutz -------------------------------------------------------------

def test_scan_data_has_catalog_associations_and_weekly_grid(db, cfg):
    prepared = prepare(db, cfg, "scan")
    assert set(prepared.data) == {"zeitraum", "signale", "assoziationen", "wochenraster"}
    assert prepared.data["signale"] == [{"id": "sig_01", "beschreibung": "Anwesenheit person_1 (home oder away)",
                                         "art": "Kategorie", "werte": ["away", "home"]}]
    top = prepared.data["assoziationen"][0]
    assert top["signal"] == "sig_01" and top["fenster_min"] == [0, 30] and top["mit_eingriff"] == top["ereignisse"]
    assert top["raum"] in ("alle Räume", "Wohnzimmer") and top["typische_aktion"].startswith("Sollwert 21")
    assert prepared.associations and "raeume" in prepared.data["wochenraster"]


def test_scan_prompt_uses_the_general_vocabulary_and_leaks_nothing(db, cfg):
    prepared = prepare(db, cfg, "scan")
    text = "\n".join(message["content"] for message in prepared.messages)
    assert "Modus: Scan" in text and "signal_state" in text and "alarm_time" in text
    assert "person_event" not in text and '"person_arrives"' not in text           # kein altes Vokabular
    for forbidden in ("svenja", "person.svenja", "u-svenja"):
        assert forbidden not in text.lower()


def test_names_in_signal_labels_abort_the_run_before_sending(db, cfg):
    from analysis.runner import PrivacyLeak
    bad = replace(cfg, signal_labels={"person.svenja": "Handy von Svenja"})
    with pytest.raises(PrivacyLeak):
        prepare(db, bad, "scan")


def test_parse_context_for_scan_knows_signals_and_limits_conditions(db, cfg):
    ctx = prepare(db, cfg, "scan").parse_ctx
    assert set(ctx.signals) == {"sig_01"} and ctx.max_conditions == 2
    assert prepare(db, cfg, "weekly").parse_ctx.signals == {} and prepare(db, cfg, "weekly").parse_ctx.max_conditions is None


def test_scan_without_findings_stops_with_a_hint(db, cfg):
    strict = replace(cfg, scan=replace(cfg.scan, min_hits=10_000))
    with pytest.raises(NothingToAnalyze, match="scan"):
        prepare(db, strict, "scan")


# --- Lauf --------------------------------------------------------------------------------

async def test_scan_run_backtests_generic_candidates_and_stores_associations(db, cfg, fake_llm):
    fake_llm.responses = [answer(ARRIVAL, ARRIVAL | {"trigger": {**ARRIVAL["trigger"], "signal": "sig_77"}})]
    summary = await run(db, cfg)
    assert summary.by_status == {"bestanden": 1, "nicht_pruefbar": 1}
    store = Store(db)
    passed = [c for c in store.list_candidates(run_id=summary.run_id) if c.status == "bestanden"][0]
    assert passed.trigger["min_prev_min"] == 180 and store.results(passed.id)["faelle"] == 30.0
    assert store.list_associations(summary.run_id)


async def test_old_modes_do_not_accept_generic_candidates(db, cfg, fake_llm):
    fake_llm.responses = [answer(ARRIVAL)]
    summary = await run(db, cfg, mode="weekly")
    assert summary.by_status == {"nicht_pruefbar": 1}


async def test_failed_scan_run_stores_no_associations(db, cfg, fake_llm):
    fake_llm.responses = [{"content": "kein json"}, {"content": "immer noch nicht"}]
    with pytest.raises(InvalidResponse):
        await run(db, cfg)
    store = Store(db)
    (row,) = store.list_runs()
    assert row["status"] == "fehlgeschlagen" and store.list_associations(row["id"]) == []


async def test_stored_generic_candidates_can_be_backtested_again(db, cfg, fake_llm):
    fake_llm.responses = [answer(ARRIVAL)]
    summary = await run(db, cfg)
    (candidate,) = Store(db).list_candidates(run_id=summary.run_id)
    assert backtest_candidate(db, cfg, candidate.id).status == "bestanden"


# --- Darstellung --------------------------------------------------------------------------

SIGNALS = {"sig_01": "Anwesenheit person_1 (home oder away)", "sig_02": "Sensor", "sig_03": "Weckzeit"}


def candidate(trigger, conditions=()):
    return Candidate(room=ROOM, trigger=trigger, conditions=list(conditions),
                     action={"type": "setpoint", "value": 21.0}, status=STATUS_OPEN)


def test_describe_generic_triggers_and_conditions(acfg):
    cfg = replace(acfg, room_names={ROOM: "Wohnzimmer"})
    pseudo = Pseudonyms((), {}, {})
    trigger = {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home",
               "min_prev_min": 180, "offset_min": 10}
    conditions = [{"type": "signal_range", "signal": "sig_02", "op": "below", "value": 8.0},
                  {"type": "signal_state", "signal": "sig_01", "op": "is_not", "value": "away"}]
    room, when, action = describe(candidate(trigger, conditions), cfg, pseudo, SIGNALS)
    assert room == "Wohnzimmer" and action == "21 °C"
    assert when == ("Anwesenheit person_1 (home oder away) wechselt zu home (vorher 180–∞ min im anderen Zustand), "
                    "+10 min wenn Sensor unter 8; Anwesenheit person_1 (home oder away) ist nicht away")
    alarm = {"type": "signal", "signal": "sig_03", "event": "alarm_time", "offset_min": -30}
    assert describe(candidate(alarm), cfg, pseudo, SIGNALS)[1] == "Weckzeit: Weckzeit erreicht, -30 min"
    assert describe(candidate(alarm), cfg, pseudo)[1].startswith("sig_03")            # ohne Katalog: die Kennung


def test_signature_groups_similar_signal_triggers():
    base = {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": 180}
    assert signature(candidate({**base, "offset_min": 5})) == signature(candidate({**base, "offset_min": 20}))
    assert signature(candidate({**base, "offset_min": 5})) != signature(candidate({**base, "offset_min": 60}))
    assert signature(candidate({**base, "value": "away"})) != signature(candidate(base))


def test_scan_intro_is_part_of_the_messages():
    messages = build_messages("scan", {"a": 1}, "V", 20, presets=[], flat_states=[], rooms=["Wohnzimmer"])
    assert messages[1]["content"].startswith("Modus: Scan")
