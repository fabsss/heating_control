from __future__ import annotations

import json
from dataclasses import dataclass

from .helpers import STOP_NAME, helper_names
from .mqtt import DEVICE, DISCOVERY, STATUS_TOPIC

MODES = ("weekly", "clusters", "scan")
NO_SELECTION = "–"
JOB_LABELS = {
    "status": "Status aktualisieren",
    "signals": "Signale anzeigen",
    "scan": "Scan (ohne Modell)",
    "llm_dry": "Analyse: Trockenlauf",
    "llm_run": "Analyse mit Modell starten",
    "candidates": "Bestandene Kandidaten anzeigen",
    "compare": "Läufe vergleichen",
    "deploy": "Kandidat im Schatten anlegen",
    "undeploy": "Regel entfernen",
    "check": "Systemprüfung",
}

SELECT_MODE = "select.homeai_mode"
SELECT_CANDIDATE = "select.homeai_candidate"
SELECT_RULE = "select.homeai_rule"
SWITCH_ARM = "switch.homeai_llm_arm"
SENSOR_JOB = "sensor.homeai_job"
SENSOR_RESULT = "sensor.homeai_result"
BINARY_CONTAINER = "binary_sensor.homeai_container"

AVAILABILITY = {"availability_topic": STATUS_TOPIC, "payload_available": "online", "payload_not_available": "offline"}


def button_entity(name: str) -> str:
    return f"button.homeai_job_{name}"


def _entry(component: str, object_id: str, name: str, **extra) -> tuple[str, dict]:
    topic = f"{DISCOVERY}/{component}/homeai/{object_id}/config"
    return topic, {"name": name, "unique_id": f"homeai_{object_id}",
                   "default_entity_id": f"{component}.homeai_{object_id}", "device": DEVICE, **extra}


def _select(object_id: str, what: str, name: str, options: list[str]) -> tuple[str, dict]:
    return _entry("select", object_id, name, options=options or [NO_SELECTION],
                  command_topic=f"homeai/sel/{what}/set", state_topic=f"homeai/sel/{what}/state", **AVAILABILITY)


def control_entities(candidate_options: list[str], rule_options: list[str]) -> list[tuple[str, dict]]:
    entries = [_entry("button", f"job_{name}", label, command_topic=f"homeai/cmd/{name}", payload_press="PRESS",
                      **AVAILABILITY) for name, label in JOB_LABELS.items()]
    entries += [
        _select("mode", "mode", "Modus der Analyse", list(MODES)),
        _select("candidate", "candidate", "Kandidat", candidate_options),
        _select("rule", "rule", "Regel", rule_options),
        _entry("switch", "llm_arm", "Kosten freigeben", command_topic="homeai/cmd/llm_arm",
               state_topic="homeai/state/llm_arm", payload_on="ON", payload_off="OFF", **AVAILABILITY),
        _entry("sensor", "job", "Auftrag", state_topic="homeai/job/state", value_template="{{ value_json.state }}",
               json_attributes_topic="homeai/job/state", **AVAILABILITY),
        _entry("sensor", "result", "Ergebnis", state_topic="homeai/job/result",
               value_template="{{ value_json.title }}", json_attributes_topic="homeai/job/result"),
        _entry("binary_sensor", "container", "Container", state_topic=STATUS_TOPIC, payload_on="online",
               payload_off="offline", device_class="connectivity"),
    ]
    return entries


async def publish_controls(bus, candidate_options: list[str], rule_options: list[str]) -> None:
    for topic, payload in control_entities(candidate_options, rule_options):
        await bus.publish(topic, json.dumps(payload, ensure_ascii=False), retain=True)


@dataclass(frozen=True)
class RuleCard:
    rule_id: str
    label: str


RESULT_CARD = {"type": "markdown", "title": "Letztes Ergebnis",
               "content": "{{ state_attr('" + SENSOR_RESULT + "', 'markdown') or 'Noch kein Ergebnis.' }}"}


def _rule_card(rule: RuleCard) -> dict:
    base = f"homeai_{rule.rule_id}"
    return {"type": "vertical-stack", "cards": [
        {"type": "entities", "title": f"Regel {rule.rule_id}: {rule.label}", "entities": [
            f"switch.{base}_aktiv", f"sensor.{base}_kennzahlen", f"sensor.{base}_trefferquote",
            f"sensor.{base}_abweichung"]},
        {"type": "markdown", "content": "{{ state_attr('sensor." + base + "_kennzahlen', 'text') or 'Noch keine Auslösung.' }}"},
    ]}


def build_dashboard(rules: list[RuleCard], room_keys: list[str]) -> dict:
    rooms = [f"input_boolean.{helper_names(key).toggle}" for key in room_keys]
    overview = {"title": "Übersicht", "path": "uebersicht", "icon": "mdi:view-dashboard", "cards": [
        {"type": "entities", "title": "Betrieb", "entities": [
            BINARY_CONTAINER, SENSOR_JOB, f"input_boolean.{STOP_NAME}", *rooms]},
        {"type": "entities", "title": "Aufträge", "entities": [button_entity("status"), button_entity("check")]},
        RESULT_CARD]}
    rule_cards = [_rule_card(rule) for rule in rules] or [
        {"type": "markdown", "content": "Noch keine Regel angelegt. Unter „Kandidaten“ eine bestandene Regel wählen."}]
    return {"title": "KI-Regeln", "views": [
        overview,
        {"title": "Regeln", "path": "regeln", "icon": "mdi:robot", "cards": rule_cards},
        {"title": "Kandidaten", "path": "kandidaten", "icon": "mdi:format-list-checks", "cards": [
            {"type": "entities", "title": "Anlegen und Entfernen", "entities": [
                button_entity("candidates"), SELECT_CANDIDATE, button_entity("deploy"),
                SELECT_RULE, button_entity("undeploy")]},
            RESULT_CARD]},
        {"title": "Analyse", "path": "analyse", "icon": "mdi:chart-line", "cards": [
            {"type": "entities", "title": "Auswertung der Daten", "entities": [
                button_entity("signals"), button_entity("scan"), SELECT_MODE, button_entity("llm_dry"), SWITCH_ARM,
                button_entity("llm_run"), button_entity("compare")]},
            RESULT_CARD]},
        {"title": "System", "path": "system", "icon": "mdi:cog", "cards": [
            {"type": "entities", "title": "System", "entities": [
                SENSOR_JOB, button_entity("status"), button_entity("check")]},
            RESULT_CARD]},
    ]}
