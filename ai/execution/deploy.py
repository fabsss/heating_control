from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from analysis.candidates import describe
from analysis.config import AnalysisConfig
from analysis.models import STATUS_PASSED
from analysis.pseudonym import build_pseudonyms
from analysis.store import Store as AnalysisStore
from collector.db import Database
from collector.models import to_utc_iso, utcnow

from .config import ExecutionConfig, RoomConfig
from .controls import RuleCard, build_dashboard
from .ha_api import HaError, HaRest, HaWs
from .helpers import STOP_NAME, eingriff_automations, helper_names, helper_specs
from .models import Deployment
from .mqtt import clear_rule_entities, publish_rule_entities, rule_label
from .store import ExecStore
from .translate import TranslateError, climate_for_room, load_signal_refs, translate

log = logging.getLogger("execution")

BLUEPRINT_VERSION = "1.0"
TIME_RE = re.compile(r"^\d\d:\d\d")


class DeployError(RuntimeError):
    pass


@dataclass(frozen=True)
class HeatingRoom:
    automation_entity: str
    heating_entity: str
    flat_state: str
    ki_toggle: str | None
    winter_toggle: str | None
    schedule_entity: str | None
    nightly_time: str | None


@dataclass(frozen=True)
class DeployResult:
    rule_id: str
    automation_entity: str
    hints: list[str]


def _minutes(text: str) -> int:
    hours, minutes = text[:5].split(":")
    return int(hours) * 60 + int(minutes)


def time_hints(rule_times: list[str], flanks: set[str], nightly: str | None, window_min: int = 10) -> list[str]:
    marks = sorted({*flanks, *([nightly[:5]] if nightly else [])})
    hints = []
    for rule_time in rule_times:
        for mark in marks:
            distance = abs(_minutes(rule_time) - _minutes(mark))
            distance = min(distance, 1440 - distance)
            if distance <= window_min:
                hints.append(f"Die Regel löst um {rule_time} aus, {distance} Minuten neben der Flanke {mark} von "
                             "heating_control; bei gleichzeitigem Schalten gilt der später gesetzte Wert.")
    return hints


def _entity_input(inputs: dict, key: str) -> str | None:
    value = inputs.get(key)
    return value if isinstance(value, str) and value else None


class Deployer:
    def __init__(self, cfg: ExecutionConfig, db: Database, store: ExecStore, astore: AnalysisStore,
                 acfg: AnalysisConfig, rest: HaRest, ws: HaWs, bus, now: Callable[[], datetime] = utcnow):
        self.cfg, self.db, self.store, self.astore, self.acfg = cfg, db, store, astore, acfg
        self.rest, self.ws, self.bus, self.now = rest, ws, bus, now
        self.find_retries = 10
        self.find_delay = 0.5

    # --- Lesen der Raum-Konfiguration -----------------------------------------------------

    async def heating_room(self, room: RoomConfig) -> HeatingRoom:
        state = await self.rest.get_state(room.heating_automation)
        if state is None:
            raise DeployError(f"Die Automation {room.heating_automation} existiert nicht in Home Assistant")
        config_id = state["attributes"].get("id")
        config = await self.rest.automation_config(config_id) if config_id else None
        inputs = ((config or {}).get("use_blueprint") or {}).get("input")
        if not inputs:
            raise DeployError(f"{room.heating_automation} ist keine lesbare Blueprint-Automation")
        heating, flat = _entity_input(inputs, "heating_entities"), _entity_input(inputs, "flat_occupation_state")
        if not heating or not flat:
            raise DeployError(f"{room.heating_automation} enthält keinen Heizkörper oder Wohnungszustand")
        return HeatingRoom(room.heating_automation, heating, flat, _entity_input(inputs, "ki_rules_toggle"),
                           _entity_input(inputs, "heating_on_off"), _entity_input(inputs, "schedule_entity"),
                           str(inputs.get("nightly_eco_time") or "01:00:00"))

    # --- init ------------------------------------------------------------------------------------

    async def init(self, key: str | None = None) -> list[str]:
        lines = [f"Sammelschalter: {await self.ws.ensure_helper('input_boolean', STOP_NAME, icon='mdi:robot-off')}"]
        rooms = [self.cfg.room_by_key(key)] if key else list(self.cfg.rooms.values())
        for room in rooms:
            try:
                climate = climate_for_room(self.db, room.area)
                for domain, name, options in helper_specs(room.key):
                    await self.ws.ensure_helper(domain, name, **options)
                for config_id, config in eingriff_automations(room.key, climate):
                    await self.rest.save_automation(config_id, config)
            except (TranslateError, HaError) as exc:
                lines.append(f"{room.key}: FEHLER {exc}")
                continue
            lines.append(f"{room.key}: Setze in {room.heating_automation} die Eingabe "
                         f"'KI-Regeln erlaubt (Schalter)' auf input_boolean.{helper_names(room.key).toggle}")
        return lines

    # --- deploy ------------------------------------------------------------------------------------

    async def deploy(self, candidate_id: int) -> DeployResult:
        try:
            candidate = self.astore.get_candidate(candidate_id)
        except KeyError as exc:
            raise DeployError(f"Kandidat {candidate_id} existiert nicht") from exc
        if candidate.status != STATUS_PASSED:
            raise DeployError(f"Kandidat {candidate_id} hat den Status {candidate.status}, nötig ist bestanden")
        rule_id = f"r{candidate_id}"
        existing = self.store.get_deployment(rule_id)
        if existing is not None and existing.state != "entfernt":
            raise DeployError(f"Die Regel {rule_id} ist bereits angelegt")
        room = self.cfg.rooms.get(candidate.room)
        if room is None:
            raise DeployError(f"Der Raum {candidate.room} ist in execution.yaml nicht eingetragen")
        info = await self.heating_room(room)
        if not info.ki_toggle:
            raise DeployError(f"In {room.heating_automation} fehlt die Eingabe ki_rules_toggle. "
                              f"Zuerst `execution init --room {room.key}` ausführen und die Eingabe setzen.")
        try:
            climate = climate_for_room(self.db, room.area)
            rule = translate(candidate, load_signal_refs(self.db), setpoint_min=self.cfg.setpoint_min,
                             setpoint_max=self.cfg.setpoint_max)
        except TranslateError as exc:
            raise DeployError(str(exc)) from exc
        if info.heating_entity != climate:
            raise DeployError(f"Der Heizkörper {info.heating_entity} in {room.heating_automation} stimmt nicht überein "
                              f"mit dem aufgezeichneten {climate}")
        if rule.action_kind == "preset":
            climate_state = await self.rest.get_state(info.heating_entity)
            offered = ((climate_state or {}).get("attributes") or {}).get("preset_modes")
            if offered and rule.action_preset not in offered:
                raise DeployError(f"Der Heizkörper {info.heating_entity} bietet das Preset {rule.action_preset!r} nicht "
                                  f"an (angeboten: {', '.join(offered)})")
        pseudo = build_pseudonyms(self.db)
        place, when, action = describe(candidate, self.acfg, pseudo, self.astore.signal_descriptions())
        description = f"{place}: {when} -> {action}"
        names = helper_names(room.key)
        config_id = f"homeai_rule_{rule_id}"
        inputs = {
            "rule_id": rule_id, "heating_entity": info.heating_entity, "room_toggle": info.ki_toggle,
            "rule_toggle": f"switch.homeai_{rule_id}_aktiv", "stop_toggle": f"input_boolean.{STOP_NAME}",
            "flat_state": info.flat_state, "blocked_states": list(self.cfg.blocked_states),
            "heating_automation": info.automation_entity, "last_intervention": f"input_datetime.{names.last_intervention}",
            "min_distance_min": self.cfg.min_distance_min, "setpoint_min": self.cfg.setpoint_min,
            "setpoint_max": self.cfg.setpoint_max, "delay_min": rule.delay_min, "action_kind": rule.action_kind,
            "action_preset": rule.action_preset, "action_setpoint": rule.action_setpoint,
            "rule_triggers": rule.triggers, "rule_conditions": rule.conditions,
        }
        if info.winter_toggle:
            inputs["winter_toggle"] = info.winter_toggle
        automation = {"alias": f"KI-Regel {rule_id}: {description}", "description": candidate.hypothesis or "",
                      "use_blueprint": {"path": self.cfg.blueprint_path, "input": inputs}}
        hints = await self._hints(rule, info)
        label = rule_label(rule_id, description)
        saved = published = False
        try:
            await publish_rule_entities(self.bus, rule_id, label)
            published = True
            await self.rest.save_automation(config_id, automation)
            saved = True
            if await self.rest.automation_config(config_id) is None:
                raise DeployError("Home Assistant meldet die angelegte Automation nicht zurück")
            entity = await self.rest.find_automation_entity(config_id, retries=self.find_retries,
                                                            delay=self.find_delay)
            if entity is None:
                raise DeployError("Die angelegte Automation erscheint nicht in Home Assistant")
            self.store.add_deployment(Deployment(
                rule_id, candidate_id, candidate.room, entity, config_id, climate, "schatten", BLUEPRINT_VERSION,
                description, to_utc_iso(self.now())))
        except Exception:
            if saved:
                try:
                    await self.rest.delete_automation(config_id)
                except HaError:
                    log.exception("Die halb angelegte Automation %s konnte nicht entfernt werden", config_id)
            if published:
                await clear_rule_entities(self.bus, rule_id)
            raise
        await self.refresh_dashboard()
        return DeployResult(rule_id, entity, hints)

    async def _hints(self, rule, info: HeatingRoom) -> list[str]:
        times = [trigger["at"][:5] for trigger in rule.triggers
                 if trigger.get("trigger") == "time" and TIME_RE.match(str(trigger.get("at", "")))]
        if not times:
            return []
        flanks: set[str] = set()
        if info.schedule_entity:
            flanks = await self.ws.schedule_flanks(info.schedule_entity.split(".", 1)[1])
        return time_hints(times, flanks, info.nightly_time)

    # --- undeploy ----------------------------------------------------------------------------------

    async def undeploy(self, rule_id: str) -> None:
        deployment = self.store.get_deployment(rule_id)
        if deployment is None:
            raise DeployError(f"Die Regel {rule_id} ist unbekannt")
        if deployment.state == "entfernt":
            raise DeployError(f"Die Regel {rule_id} ist bereits entfernt")
        await self.rest.delete_automation(deployment.config_id)
        await clear_rule_entities(self.bus, rule_id)
        self.store.set_deployment_state(rule_id, "entfernt", self.now())
        await self.refresh_dashboard()

    # --- Dashboard und Prüfung -------------------------------------------------------------------

    async def refresh_dashboard(self) -> None:
        cards = [RuleCard(item.rule_id, item.description) for item in self.store.list_deployments()]
        await self.ws.ensure_dashboard(self.cfg.dashboard_path, "KI-Regeln", "mdi:robot")
        await self.ws.save_dashboard(self.cfg.dashboard_path,
                                     build_dashboard(cards, [room.key for room in self.cfg.rooms.values()]))

    async def check(self) -> list[str]:
        lines: list[str] = []

        def report(good: bool, text: str) -> None:
            lines.append(("OK    " if good else "FEHLT ") + text)

        report(self.cfg.blueprint_path in await self.ws.blueprint_paths(), f"Blueprint {self.cfg.blueprint_path} in Home Assistant")
        report(await self.rest.get_state(f"input_boolean.{STOP_NAME}") is not None, f"Sammelschalter input_boolean.{STOP_NAME}")
        for room in self.cfg.rooms.values():
            try:
                info = await self.heating_room(room)
            except DeployError as exc:
                report(False, f"{room.key}: {exc}")
                continue
            report(bool(info.ki_toggle), f"{room.key}: ki_rules_toggle in {room.heating_automation}")
            if info.ki_toggle:
                report(await self.rest.get_state(info.ki_toggle) is not None, f"{room.key}: Raumschalter {info.ki_toggle}")
            names = helper_names(room.key)
            for entity in (f"input_datetime.{names.last_intervention}", f"input_datetime.{names.last_auto}"):
                report(await self.rest.get_state(entity) is not None, f"{room.key}: Helfer {entity}")
            for config_id in (f"homeai_aufrufe_{room.key}", f"homeai_beobachter_{room.key}"):
                report(await self.rest.automation_config(config_id) is not None, f"{room.key}: Automation {config_id}")
        for deployment in self.store.list_deployments():
            report(await self.rest.automation_config(deployment.config_id) is not None,
                   f"Regel {deployment.rule_id}: Automation {deployment.config_id}")
        return lines
