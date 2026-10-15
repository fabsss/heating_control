from __future__ import annotations

import re
from dataclasses import dataclass

STOP_NAME = "ki_regeln_alle_schatten"
WAIT_S = 3
CALL_MEMORY_S = 6


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


@dataclass(frozen=True)
class HelperNames:
    toggle: str              # input_boolean: KI-Regeln erlaubt (Raumschalter)
    last_intervention: str   # input_datetime: letzter Eingriff von Benutzer oder Gerät
    last_auto: str           # input_datetime: letzter Aufruf einer Automation


def helper_names(key: str) -> HelperNames:
    key = slug(key)
    return HelperNames(f"ki_regeln_{key}", f"ki_eingriff_{key}", f"ki_aufruf_{key}")


def helper_specs(key: str) -> list[tuple[str, str, dict]]:
    names = helper_names(key)
    return [
        ("input_boolean", names.toggle, {"icon": "mdi:robot"}),
        ("input_datetime", names.last_intervention, {"has_date": True, "has_time": True, "icon": "mdi:hand-back-right"}),
        ("input_datetime", names.last_auto, {"has_date": True, "has_time": True, "icon": "mdi:robot-outline"}),
    ]


def _stamp(entity: str) -> dict:
    return {"action": "input_datetime.set_datetime", "target": {"entity_id": entity},
            "data": {"timestamp": "{{ as_timestamp(now()) }}"}}


def eingriff_automations(key: str, climate_entity: str) -> list[tuple[str, dict]]:
    """Zwei Hilfsautomationen je Raum: Aufrufe merken und Eingriffe am Gerät erkennen (läuft ohne Container)."""
    names = helper_names(key)
    intervention = f"input_datetime.{names.last_intervention}"
    auto = f"input_datetime.{names.last_auto}"
    calls = {
        "alias": f"KI Aufrufe {slug(key)}",
        "description": "Merkt, ob ein Dienstaufruf an den Heizkörper von einem Benutzer oder einer Automation kam.",
        "mode": "queued",
        "max": 20,
        "triggers": [{"trigger": "event", "event_type": "call_service", "event_data": {"domain": "climate"}}],
        "conditions": [{"condition": "template", "value_template": (
            "{% set ids = trigger.event.data.service_data.entity_id | default([], true) %}"
            "{{ '" + climate_entity + "' in ([ids] if ids is string else ids) }}")}],
        "actions": [{"choose": [{
            "conditions": [{"condition": "template", "value_template": (
                "{{ trigger.event.context.user_id is not none and trigger.event.context.parent_id is none }}")}],
            "sequence": [_stamp(intervention)]}],
            "default": [_stamp(auto)]}],
    }
    observer = {
        "alias": f"KI Beobachter {slug(key)}",
        "description": "Erkennt Änderungen am Heizkörper ohne zugehörigen Dienstaufruf (Drehen am Gerät) als Eingriff.",
        "mode": "queued",
        "max": 10,
        "triggers": [{"trigger": "state", "entity_id": climate_entity}],
        "conditions": [{"condition": "template", "value_template": (
            "{{ trigger.from_state is not none"
            " and trigger.from_state.state not in ['unavailable', 'unknown']"
            " and trigger.to_state.state not in ['unavailable', 'unknown']"
            " and (trigger.from_state.state != trigger.to_state.state"
            " or trigger.from_state.attributes.get('temperature') != trigger.to_state.attributes.get('temperature')"
            " or trigger.from_state.attributes.get('preset_mode') != trigger.to_state.attributes.get('preset_mode')) }}")}],
        "actions": [
            {"delay": {"seconds": WAIT_S}},
            {"condition": "template", "value_template": (
                f"{{{{ as_timestamp(now()) - state_attr('{intervention}', 'timestamp') | float(0) > {CALL_MEMORY_S}"
                f" and as_timestamp(now()) - state_attr('{auto}', 'timestamp') | float(0) > {CALL_MEMORY_S} }}}}")},
            _stamp(intervention),
        ],
    }
    return [(f"homeai_aufrufe_{slug(key)}", calls), (f"homeai_beobachter_{slug(key)}", observer)]
