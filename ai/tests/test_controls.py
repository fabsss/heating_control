import json

from execution.controls import (BINARY_CONTAINER, JOB_LABELS, MODES, NO_SELECTION, SELECT_CANDIDATE, SELECT_MODE,
                                SELECT_RULE, SENSOR_JOB, SENSOR_RESULT, SWITCH_ARM, RuleCard, build_dashboard,
                                button_entity, control_entities, publish_controls)
from execution.helpers import STOP_NAME, helper_names
from execution.mqtt import InMemoryBus, rule_entities


def entities(candidates=("7: Bad Mo-Fr 06:30 -> Preset comfort",), rules=("r7: Bad",)):
    return control_entities(list(candidates), list(rules))


def by_id():
    return {payload["default_entity_id"]: payload for _, payload in entities()}


def test_every_job_has_a_button_with_its_command_topic():
    found = by_id()
    for name in JOB_LABELS:
        payload = found[button_entity(name)]
        assert payload["command_topic"] == f"homeai/cmd/{name}" and payload["payload_press"] == "PRESS"
    assert set(JOB_LABELS) == {"status", "signals", "scan", "llm_dry", "llm_run", "candidates", "latest", "compare",
                               "deploy", "undeploy", "check"}


def test_the_dashboard_offers_the_latest_run_next_to_the_passed_candidates():
    views = {view["path"]: str(view) for view in build_dashboard([], ["bad"])["views"]}
    assert button_entity("latest") in views["kandidaten"] and button_entity("latest") in views["analyse"]


def test_selects_carry_their_options_and_fall_back_to_a_placeholder():
    found = by_id()
    assert found[SELECT_MODE]["options"] == list(MODES) and found[SELECT_MODE]["command_topic"] == "homeai/sel/mode/set"
    assert found[SELECT_CANDIDATE]["options"] == ["7: Bad Mo-Fr 06:30 -> Preset comfort"]
    assert found[SELECT_RULE]["state_topic"] == "homeai/sel/rule/state"
    empty = {payload["default_entity_id"]: payload for _, payload in control_entities([], [])}
    assert empty[SELECT_CANDIDATE]["options"] == [NO_SELECTION] and empty[SELECT_RULE]["options"] == [NO_SELECTION]


def test_cost_switch_and_status_entities():
    found = by_id()
    arm = found[SWITCH_ARM]
    assert (arm["command_topic"], arm["state_topic"]) == ("homeai/cmd/llm_arm", "homeai/state/llm_arm")
    assert found[SENSOR_JOB]["state_topic"] == "homeai/job/state" and found[SENSOR_JOB]["json_attributes_topic"] == "homeai/job/state"
    assert found[SENSOR_RESULT]["state_topic"] == "homeai/job/result"
    assert found[BINARY_CONTAINER]["state_topic"] == "homeai/status" and found[BINARY_CONTAINER]["payload_off"] == "offline"
    assert "availability_topic" not in found[BINARY_CONTAINER] and "availability_topic" not in found[SENSOR_RESULT]


def test_controls_become_unavailable_with_the_container_but_rule_switches_do_not():
    found = by_id()
    for entity in (button_entity("scan"), SELECT_MODE, SWITCH_ARM, SENSOR_JOB):
        assert found[entity]["availability_topic"] == "homeai/status"
    for _, payload in rule_entities("r7", "Bad"):
        if payload["default_entity_id"].startswith("switch."):
            assert "availability_topic" not in payload


def test_unique_ids_and_topics_are_unique():
    pairs = entities()
    assert len({payload["unique_id"] for _, payload in pairs}) == len(pairs)
    assert len({topic for topic, _ in pairs}) == len(pairs)


async def test_publish_controls_retains_the_discovery():
    bus = InMemoryBus()
    await publish_controls(bus, ["7: x"], ["r7: y"])
    assert len(bus.retained) == len(entities()) and all(message.retain for message in bus.published)
    assert json.loads(bus.retained["homeassistant/select/homeai/candidate/config"])["options"] == ["7: x"]


def referenced(dashboard) -> set[str]:
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "entities":
                    found.update(item if isinstance(item, str) else item["entity"] for item in value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(dashboard)
    return found


def test_dashboard_references_only_existing_entities():
    dashboard = build_dashboard([RuleCard("r7", "Bad: Mo-Fr 06:30 -> Preset comfort")], ["bad", "wohnzimmer"])
    known = set(by_id()) | {payload["default_entity_id"] for _, payload in rule_entities("r7", "x")}
    known |= {f"input_boolean.{STOP_NAME}"} | {f"input_boolean.{helper_names(key).toggle}" for key in ("bad", "wohnzimmer")}
    assert referenced(dashboard) <= known
    assert {view["title"] for view in dashboard["views"]} == {"Übersicht", "Regeln", "Kandidaten", "Analyse", "System"}
    rules_view = next(view for view in dashboard["views"] if view["title"] == "Regeln")
    assert "switch.homeai_r7_aktiv" in referenced(rules_view) and "Mo-Fr 06:30" in json.dumps(rules_view)


def test_dashboard_without_rules_says_so():
    dashboard = build_dashboard([], ["bad"])
    rules_view = next(view for view in dashboard["views"] if view["title"] == "Regeln")
    assert "Noch keine Regel" in json.dumps(rules_view, ensure_ascii=False)
