from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from collector.models import Change, EntityRule, to_utc_iso
from execution.models import Deployment, RuleEvent
from execution.outcomes import evaluate, rule_stats, same_target
from execution.store import ExecStore

T0 = datetime(2026, 10, 15, 4, 30, tzinfo=timezone.utc)        # Auslösung um 06:30 Ortszeit
CLIMATE = EntityRule("climate.bt", "climate", "heating", attrs=("temperature", "preset_mode"))
OWN = "automation.ki_regel_r5"
COMFORT = {"kind": "preset", "value": "comfort"}


@pytest.fixture
def world(db):
    db.upsert_entity(CLIMATE, "bad", T0)
    add(db, -300, 17, "eco")                                   # Ausgangszustand
    return db


def add(db, minutes, temperature, preset, origin=None, automation=None, state="heat"):
    ts = T0 + timedelta(minutes=minutes)
    db.insert_changes([Change(ts, "climate.bt", state, {"temperature": temperature, "preset_mode": preset},
                              f"c{minutes}")], "live")
    if origin is not None:
        sid = db.conn.execute("SELECT id FROM state_changes WHERE ts_utc = ?", (to_utc_iso(ts),)).fetchone()["id"]
        db.conn.execute("INSERT INTO change_origin(state_change_id, origin, via, actor, automation) "
                        "VALUES (?, ?, 'kontext', 'u1', ?)", (sid, origin, automation))
        db.conn.commit()


def deployment():
    return Deployment("r5", 5, "bad", OWN, "homeai_rule_r5", "climate.bt", "schatten", "1.0", "x",
                      "2026-10-15T00:00:00.000000+00:00")


def event(result="nicht_ausgefuehrt", reason="schatten", target=COMFORT):
    return RuleEvent("r5", to_utc_iso(T0), "schatten", result, reason, target, {}, "{}", 1)


def verdict(db, ecfg, **kwargs):
    return evaluate(db, event(**kwargs), deployment(), ecfg)


def test_same_target_for_presets_and_setpoints():
    assert same_target(COMFORT, {"preset_mode": "comfort", "temperature": 21.0}, 0.5)
    assert not same_target(COMFORT, {"preset_mode": "eco", "temperature": 21.0}, 0.5)
    setpoint = {"kind": "setpoint", "value": 21.0}
    assert same_target(setpoint, {"temperature": 21.4}, 0.5) and not same_target(setpoint, {"temperature": 21.6}, 0.5)
    assert not same_target(setpoint, {"temperature": None}, 0.5)


def test_shadow_without_any_intervention_is_neutral(world, ecfg):
    assert verdict(world, ecfg).outcome == "neutral"


def test_user_does_the_same_after_the_rule(world, ecfg):
    add(world, 10, 21, "comfort", origin="benutzer")
    result = verdict(world, ecfg)
    assert (result.outcome, result.lage, result.abstand_min) == ("treffer", "nachher", 10.0)


def test_user_did_it_before_the_rule(world, ecfg):
    add(world, -20, 21, "comfort", origin="benutzer")
    result = verdict(world, ecfg)
    assert (result.outcome, result.lage, result.abstand_min) == ("treffer", "vorher", -20.0)


def test_device_interventions_count_like_users(world, ecfg):
    add(world, 5, 21, "comfort", origin="geraet_unklar")
    assert verdict(world, ecfg).outcome == "treffer"


def test_different_target_is_a_false_trigger_with_the_deviation(world, ecfg):
    add(world, 5, 19, "eco", origin="benutzer")
    result = verdict(world, ecfg)
    assert (result.outcome, result.deviation) == ("fehlausloesung", "preset:eco")


def test_the_last_intervention_in_the_window_counts(world, ecfg):
    add(world, 5, 21, "comfort", origin="benutzer")
    add(world, 30, 17, "eco", origin="benutzer")
    assert verdict(world, ecfg).outcome == "fehlausloesung"


def test_an_automation_in_between_interrupts(world, ecfg):
    add(world, 20, 18, "eco", origin="automation", automation="automation.heating_control_bathroom")
    add(world, 50, 21, "comfort", origin="benutzer")
    assert verdict(world, ecfg).outcome == "unterbrochen"


def test_an_automation_between_a_prior_intervention_and_the_trigger_interrupts(world, ecfg):
    add(world, -30, 21, "comfort", origin="benutzer")
    add(world, -10, 17, "eco", origin="automation", automation="automation.heating_control_bathroom")
    assert verdict(world, ecfg).outcome == "unterbrochen"


def test_an_automation_after_the_last_intervention_does_not_matter(world, ecfg):
    add(world, 10, 21, "comfort", origin="benutzer")
    add(world, 50, 17, "eco", origin="automation", automation="automation.heating_control_bathroom")
    assert verdict(world, ecfg).outcome == "treffer"


def test_interventions_outside_the_window_are_ignored(world, ecfg):
    add(world, 91, 21, "comfort", origin="benutzer")
    assert verdict(world, ecfg).outcome == "neutral"
    other = ecfg
    add(world, -61, 19, "eco", origin="benutzer")
    assert verdict(world, other).outcome == "neutral"


def test_mode_only_changes_are_not_interventions(world, ecfg):
    add(world, 5, 17, "eco", origin="benutzer", state="off")
    assert verdict(world, ecfg).outcome == "neutral"


def test_setpoint_target_uses_the_tolerance(world, ecfg):
    add(world, 5, 21.4, "eco", origin="benutzer")
    target = {"kind": "setpoint", "value": 21.0}
    assert verdict(world, ecfg, target=target).outcome == "treffer"


def test_active_rule_confirmed_without_intervention_and_own_change_is_ignored(world, ecfg):
    add(world, 0.1, 21, "comfort", origin="automation", automation=OWN)
    assert verdict(world, ecfg, result="ausgefuehrt", reason="").outcome == "bestaetigt"


def test_own_change_without_a_known_automation_is_tolerated_for_a_few_seconds(world, ecfg):
    add(world, 0.1, 21, "comfort", origin="automation", automation=None)
    assert verdict(world, ecfg, result="ausgefuehrt", reason="").outcome == "bestaetigt"


def test_active_rule_corrected_by_the_user(world, ecfg):
    add(world, 0.1, 21, "comfort", origin="automation", automation=OWN)
    add(world, 20, 18, "eco", origin="benutzer")
    result = verdict(world, ecfg, result="ausgefuehrt", reason="")
    assert (result.outcome, result.deviation) == ("korrigiert", "preset:eco")


def test_active_rule_interrupted_by_another_automation(world, ecfg):
    add(world, 0.1, 21, "comfort", origin="automation", automation=OWN)
    add(world, 30, 17, "eco", origin="automation", automation="automation.heating_control_bathroom")
    assert verdict(world, ecfg, result="ausgefuehrt", reason="").outcome == "unterbrochen"


def test_active_rule_held_back_by_an_earlier_intervention_is_judged_like_shadow(world, ecfg):
    add(world, -20, 21, "comfort", origin="benutzer")
    result = verdict(world, ecfg, reason="handeingriff")
    assert (result.outcome, result.lage) == ("treffer", "vorher")


def test_reasons_that_are_not_scored(world, ecfg):
    assert verdict(world, ecfg, reason="wohnungszustand") is None
    assert verdict(world, ecfg, reason="nicht_erreichbar") is None
    assert verdict(world, ecfg, reason="bereits_erreicht").outcome == "ohne_wirkung"


# --- Kennzahlen ----------------------------------------------------------------------

def fill(store: ExecStore, outcomes: list[tuple[str, str, str, float | None, str | None]]):
    """(outcome, reason, lage, abstand, deviation) je Ereignis."""
    for index, (outcome, reason, lage, distance, deviation) in enumerate(outcomes):
        ts = to_utc_iso(T0 + timedelta(days=index))
        store.add_event(RuleEvent("r5", ts, "schatten", "nicht_ausgefuehrt", reason, COMFORT, {}, "{}"))
    for event_row, (outcome, reason, lage, distance, deviation) in zip(store.pending_events(), outcomes):
        store.save_outcome(event_row.id, outcome, T0, T0, lage=lage, abstand_min=distance, deviation=deviation)


@pytest.fixture
def store(db):
    return ExecStore(db)


def test_stats_with_too_little_data(store, ecfg):
    fill(store, [("treffer", "", "nachher", 10.0, None)] * 6
         + [("fehlausloesung", "", "nachher", 5.0, "preset:eco")] * 2
         + [("neutral", "schatten", None, None, None)] * 3
         + [("ohne_wirkung", "bereits_erreicht", None, None, None)])
    stats = rule_stats(store, "r5", ecfg)
    assert (stats.events, stats.scored, stats.good, stats.treffer, stats.fehlausloesung) == (12, 8, 6, 6, 2)
    assert stats.trefferquote == 0.75 and stats.korrekturquote is None
    assert stats.reife == "zu_wenig_daten"
    assert stats.text == "6 von 8 gewertet richtig, 3 ohne Reaktion, 1 ohne Wirkung"
    assert stats.abweichung == "preset:eco" and stats.median_abstand_min == 10.0
    assert stats.to_payload()["trefferquote_pct"] == 75.0


def test_stats_turn_green_or_red_with_enough_scored_cases(store, ecfg):
    fill(store, [("treffer", "", "vorher", -20.0, None)] * 9 + [("fehlausloesung", "", "nachher", 4.0, "19.0")])
    stats = rule_stats(store, "r5", ecfg)
    assert (stats.scored, stats.reife, stats.anteil_vorher) == (10, "gruen", 0.9)
    assert rule_stats(store, "r5", replace(ecfg, max_fail_rate=0.05)).reife == "rot"


def test_stats_for_active_rules_and_blocked_events(store, ecfg):
    fill(store, [("bestaetigt", "", None, None, None)] * 7 + [("korrigiert", "", "nachher", 12.0, "18.0")] * 2
         + [("neutral", "handeingriff", None, None, None), ("unterbrochen", "", None, None, None)])
    stats = rule_stats(store, "r5", replace(ecfg, min_scored=9))
    assert (stats.korrigiert, stats.bestaetigt, stats.ausgebremst, stats.unterbrochen) == (2, 7, 1, 1)
    assert stats.korrekturquote == pytest.approx(2 / 9) and stats.reife == "rot"
    assert stats.reasons == {"handeingriff": 1}


def test_stats_of_a_rule_without_events(store, ecfg):
    stats = rule_stats(store, "r5", ecfg)
    assert (stats.events, stats.scored, stats.reife, stats.trefferquote) == (0, 0, "zu_wenig_daten", None)
    assert stats.text == "0 von 0 gewertet richtig, 0 ohne Reaktion, 0 ohne Wirkung"
