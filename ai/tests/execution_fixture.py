from __future__ import annotations

from analysis.models import STATUS_PASSED, Candidate
from collector.models import EntityRule
from execution.helpers import slug
from tests.ha_template import utc

AREA = "a05f1cd02cf34142af915331271a6986"            # Bad
CLIMATE = "climate.better_thermostat_bathroom"
HEATING_AUTOMATION = "automation.heating_control_bathroom"
TOGGLE = "input_boolean.ki_regeln_bad"
ARRIVAL = {"type": "signal", "signal": "sig_01", "event": "to_state", "value": "home", "min_prev_min": 180,
           "offset_min": 0}


class Helpers:
    """Merkt die per WebSocket angelegten Helfer (input_boolean, input_datetime)."""

    def __init__(self, fake_hass):
        self.items: dict[str, list[dict]] = {"input_boolean": [], "input_datetime": []}
        self.created: list[tuple[str, str]] = []
        for domain in self.items:
            fake_hass.responses[f"{domain}/list"] = self._lister(domain)
            fake_hass.responses[f"{domain}/create"] = self._creator(domain)

    def _lister(self, domain):
        return lambda message: list(self.items[domain])

    def _creator(self, domain):
        def create(message):
            item = {"id": slug(message["name"]), "name": message["name"]}
            self.items[domain].append(item)
            self.created.append((domain, message["name"]))
            return item
        return create


class Dashboards:
    def __init__(self, fake_hass):
        self.items: list[dict] = []
        self.saved: list[dict] = []
        fake_hass.responses["lovelace/dashboards/list"] = lambda message: list(self.items)
        fake_hass.responses["lovelace/dashboards/create"] = lambda message: self.items.append(
            {"url_path": message["url_path"]}) or {}
        fake_hass.responses["lovelace/config/save"] = lambda message: self.saved.append(message) or None


def seed_database(db, astore, *, trigger=None, room=AREA, status=STATUS_PASSED) -> int:
    now = utc(2026, 1, 1)
    if db.conn.execute("SELECT 1 FROM entities WHERE entity_id = ?", (CLIMATE,)).fetchone() is None:
        db.upsert_entity(EntityRule(CLIMATE, "climate", "heating", attrs=("temperature", "preset_mode")), AREA, now)
        db.upsert_entity(EntityRule("person.fabian", "person", "shared"), None, now)
        handles = astore.assign_handles(["person.fabian"])
        astore.update_signal(handles["person.fabian"], "person", "categorical", "Anwesenheit person_1")
    run_id = astore.start_run("scan", "m", {}, now)
    candidate = Candidate(room=room, trigger=trigger or ARRIVAL, action={"type": "preset", "value": "comfort"},
                          status=status)
    candidate_id = astore.add_candidate(run_id, candidate)
    astore.commit()
    return candidate_id


def seed_home_assistant(fake_rest, fake_hass, *, toggle=TOGGLE):
    helpers, dashboards = Helpers(fake_hass), Dashboards(fake_hass)
    inputs = {"heating_entities": CLIMATE, "flat_occupation_state": "input_select.flat_occupation_state",
              "heating_on_off": "input_boolean.heating_season", "schedule_entity": "schedule.heating_schedule_bathroom"}
    if toggle:
        inputs["ki_rules_toggle"] = toggle
    fake_rest.set_state(CLIMATE, "heat", preset_modes=["none", "eco", "comfort"], temperature=17.0, preset_mode="eco")
    fake_rest.set_state(HEATING_AUTOMATION, "on", id="hc_bad")
    fake_rest.automations["hc_bad"] = {"alias": "Heating Control - Bathroom",
                                       "use_blueprint": {"path": "heating_control.yaml", "input": inputs}}
    fake_hass.responses["blueprint/list"] = {"fabsss/ki_regel.yaml": {}}
    fake_hass.responses["schedule/list"] = [
        {"id": "heating_schedule_bathroom", "monday": [{"from": "06:30:00", "to": "08:00:00"}]}]
    return helpers, dashboards
