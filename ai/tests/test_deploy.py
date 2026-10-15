import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from analysis.store import Store as AnalysisStore
from collector.hass_ws import HassClient
from execution.deploy import DeployError, Deployer, time_hints
from execution.ha_api import HaError, HaRest, HaWs
from execution.helpers import STOP_NAME
from execution.mqtt import InMemoryBus, rule_discovery_topics
from execution.store import ExecStore
from tests.execution_fixture import (ARRIVAL, CLIMATE, HEATING_AUTOMATION, TOGGLE, seed_database,
                                     seed_home_assistant)
from tests.ha_template import utc

NOW = utc(2026, 10, 15, 12, 0)


@pytest_asyncio.fixture
async def world(db, acfg, ecfg, fake_rest, fake_hass):
    astore, store = AnalysisStore(db), ExecStore(db)
    candidate_id = seed_database(db, astore)
    helpers, dashboards = seed_home_assistant(fake_rest, fake_hass)
    rest = HaRest(fake_rest.base, "secret")
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    bus = InMemoryBus()
    deployer = Deployer(ecfg, db, store, astore, acfg, rest, HaWs(client), bus, now=lambda: NOW)
    deployer.find_delay = 0
    try:
        yield SimpleNamespace(deployer=deployer, store=store, astore=astore, bus=bus, fake_rest=fake_rest,
                              helpers=helpers, dashboards=dashboards, candidate_id=candidate_id, db=db)
    finally:
        await client.close()
        await rest.close()


# --- init ----------------------------------------------------------------------------

async def test_init_creates_helpers_and_automations_once(world):
    lines = await world.deployer.init("bad")
    assert sorted(name for _, name in world.helpers.created) == sorted(
        [STOP_NAME, "ki_regeln_bad", "ki_eingriff_bad", "ki_aufruf_bad"])
    assert {"homeai_aufrufe_bad", "homeai_beobachter_bad"} <= set(world.fake_rest.automations)
    assert any("input_boolean.ki_regeln_bad" in line and HEATING_AUTOMATION in line for line in lines)
    await world.deployer.init("bad")
    assert len(world.helpers.created) == 4


async def test_init_without_a_key_reports_rooms_without_a_thermostat(world):
    lines = await world.deployer.init()
    assert any(line.startswith("schlafzimmer: FEHLER") for line in lines)
    assert "homeai_aufrufe_bad" in world.fake_rest.automations


# --- deploy ----------------------------------------------------------------------------

async def test_deploy_creates_the_rule_in_shadow_mode(world):
    result = await world.deployer.deploy(world.candidate_id)
    assert result.rule_id == "r1" and result.automation_entity.startswith("automation.ki_regel_r1")
    blueprint = world.fake_rest.automations["homeai_rule_r1"]["use_blueprint"]
    assert blueprint["path"] == "homeai/ki_regel.yaml"
    inputs = blueprint["input"]
    assert inputs["rule_id"] == "r1" and inputs["heating_entity"] == CLIMATE
    assert inputs["rule_toggle"] == "switch.homeai_r1_aktiv" and inputs["room_toggle"] == TOGGLE
    assert inputs["stop_toggle"] == f"input_boolean.{STOP_NAME}"
    assert inputs["last_intervention"] == "input_datetime.ki_eingriff_bad"
    assert inputs["heating_automation"] == HEATING_AUTOMATION
    assert inputs["flat_state"] == "input_select.flat_occupation_state"
    assert inputs["winter_toggle"] == "input_boolean.heating_season"
    assert inputs["blocked_states"] == ["Vacation"] and inputs["min_distance_min"] == 60
    assert (inputs["setpoint_min"], inputs["setpoint_max"]) == (15.0, 25.0)
    assert (inputs["action_kind"], inputs["action_preset"]) == ("preset", "comfort")
    assert inputs["rule_triggers"][0]["entity_id"] == "person.fabian"
    deployment = world.store.get_deployment("r1")
    assert (deployment.state, deployment.climate_entity, deployment.config_id) == ("schatten", CLIMATE, "homeai_rule_r1")
    assert deployment.ha_automation == result.automation_entity and "Bad" in deployment.description
    assert set(world.bus.retained) == set(rule_discovery_topics("r1"))
    assert "switch.homeai_r1_aktiv" in json.dumps(world.dashboards.saved[-1]["config"])


async def test_deploy_without_a_winter_switch_omits_the_input(world):
    del world.fake_rest.automations["hc_bad"]["use_blueprint"]["input"]["heating_on_off"]
    await world.deployer.deploy(world.candidate_id)
    assert "winter_toggle" not in world.fake_rest.automations["homeai_rule_r1"]["use_blueprint"]["input"]


async def test_deploy_refuses_candidates_that_did_not_pass(world):
    world.astore.set_candidate_status(world.candidate_id, "abgelehnt")
    world.astore.commit()
    with pytest.raises(DeployError, match="bestanden"):
        await world.deployer.deploy(world.candidate_id)


async def test_deploy_needs_the_toggle_in_heating_control(world):
    del world.fake_rest.automations["hc_bad"]["use_blueprint"]["input"]["ki_rules_toggle"]
    with pytest.raises(DeployError, match="ki_rules_toggle"):
        await world.deployer.deploy(world.candidate_id)
    assert world.store.list_deployments() == [] and world.bus.retained == {}


async def test_deploy_refuses_unknown_rooms_legacy_candidates_and_unknown_ids(world):
    other = seed_database(world.db, world.astore, room="keller")
    with pytest.raises(DeployError, match="Raum"):
        await world.deployer.deploy(other)
    legacy = seed_database(world.db, world.astore, trigger={"type": "event", "event": "person_arrives",
                                                            "person": "person.fabian"})
    with pytest.raises(DeployError, match="alte"):
        await world.deployer.deploy(legacy)
    with pytest.raises(DeployError, match="existiert nicht"):
        await world.deployer.deploy(999)


async def test_deploy_refuses_a_thermostat_that_differs_from_heating_control(world):
    world.fake_rest.automations["hc_bad"]["use_blueprint"]["input"]["heating_entities"] = "climate.other"
    with pytest.raises(DeployError, match="stimmt nicht überein"):
        await world.deployer.deploy(world.candidate_id)


async def test_deploy_refuses_a_preset_the_thermostat_does_not_offer(world):
    world.fake_rest.set_state(CLIMATE, "heat", preset_modes=["none", "eco"])
    with pytest.raises(DeployError, match="bietet das Preset"):
        await world.deployer.deploy(world.candidate_id)
    assert world.store.list_deployments() == [] and world.bus.retained == {}


async def test_deploy_twice_is_refused(world):
    await world.deployer.deploy(world.candidate_id)
    with pytest.raises(DeployError, match="bereits"):
        await world.deployer.deploy(world.candidate_id)


async def test_failed_save_leaves_nothing_behind(world):
    world.fake_rest.fail_save = True
    with pytest.raises(HaError):
        await world.deployer.deploy(world.candidate_id)
    assert world.store.list_deployments() == [] and world.bus.retained == {}
    assert "homeai_rule_r1" not in world.fake_rest.automations


async def test_deploy_reports_times_next_to_flanks(world):
    candidate = seed_database(world.db, world.astore, trigger={"type": "time", "weekdays": [0], "time": "06:35"})
    result = await world.deployer.deploy(candidate)
    assert len(result.hints) == 1 and "06:35" in result.hints[0] and "06:30" in result.hints[0]


def test_time_hints_flag_rules_next_to_flanks():
    hints = time_hints(["06:35", "12:00"], {"06:30", "18:30"}, "01:00:00")
    assert len(hints) == 1 and "06:35" in hints[0] and "06:30" in hints[0]
    assert time_hints(["01:05"], set(), "01:00:00")
    assert time_hints(["23:55"], {"00:02"}, None)
    assert time_hints([], {"06:30"}, None) == []


# --- undeploy und check ------------------------------------------------------------------

async def test_undeploy_removes_everything_but_the_history(world):
    await world.deployer.deploy(world.candidate_id)
    await world.deployer.undeploy("r1")
    assert "homeai_rule_r1" not in world.fake_rest.automations and world.bus.retained == {}
    assert world.store.get_deployment("r1").state == "entfernt"
    assert "r1" not in json.dumps(world.dashboards.saved[-1]["config"])
    with pytest.raises(DeployError, match="bereits entfernt"):
        await world.deployer.undeploy("r1")
    with pytest.raises(DeployError, match="unbekannt"):
        await world.deployer.undeploy("zz")


async def test_check_reports_what_is_missing_and_what_is_fine(world):
    lines = await world.deployer.check()
    assert any(line.startswith("FEHLT") and "Sammelschalter" in line for line in lines)
    assert any(line.startswith("OK    ") and "Blueprint homeai/ki_regel.yaml" in line for line in lines)
    await world.deployer.init("bad")
    for entity in (f"input_boolean.{STOP_NAME}", TOGGLE, "input_datetime.ki_eingriff_bad", "input_datetime.ki_aufruf_bad"):
        world.fake_rest.set_state(entity, "on")
    lines = await world.deployer.check()
    assert [line for line in lines if line.startswith("FEHLT bad:")] == []
    await world.deployer.deploy(world.candidate_id)
    del world.fake_rest.automations["homeai_rule_r1"]
    lines = await world.deployer.check()
    assert any(line.startswith("FEHLT") and "Regel r1" in line for line in lines)
