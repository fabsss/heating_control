import pytest

from collector.hass_ws import HassClient
from execution.ha_api import HaError, HaRest, HaWs, rest_base


def test_rest_base_follows_the_websocket_url():
    assert rest_base("ws://192.168.1.100:8123/api/websocket") == "http://192.168.1.100:8123"
    assert rest_base("wss://ha.example.org/api/websocket") == "https://ha.example.org"


async def test_rest_state_and_automation_roundtrip(fake_rest):
    rest = HaRest(fake_rest.base, "secret")
    try:
        assert await rest.get_state("climate.nope") is None
        fake_rest.set_state("climate.bt", "heat", temperature=21)
        assert (await rest.get_state("climate.bt"))["attributes"]["temperature"] == 21
        assert await rest.automation_config("homeai_rule_r5") is None
        await rest.save_automation("homeai_rule_r5", {"alias": "KI-Regel r5", "use_blueprint": {"path": "x"}})
        assert (await rest.automation_config("homeai_rule_r5"))["alias"] == "KI-Regel r5"
        assert await rest.find_automation_entity("homeai_rule_r5") == "automation.ki_regel_r5"
        await rest.delete_automation("homeai_rule_r5")
        await rest.delete_automation("homeai_rule_r5")           # zweites Löschen ist kein Fehler
        assert await rest.automation_config("homeai_rule_r5") is None
        assert await rest.find_automation_entity("homeai_rule_r5", retries=1, delay=0) is None
    finally:
        await rest.close()


async def test_rest_errors_carry_the_status(fake_rest):
    fake_rest.fail_save = True
    rest = HaRest(fake_rest.base, "secret")
    try:
        with pytest.raises(HaError, match="500"):
            await rest.save_automation("x", {"alias": "a"})
    finally:
        await rest.close()
    wrong = HaRest(fake_rest.base, "falsch")
    try:
        with pytest.raises(HaError, match="401"):
            await wrong.get_state("climate.bt")
    finally:
        await wrong.close()


async def test_ensure_helper_creates_once_and_returns_the_entity_id(fake_hass):
    created = []
    fake_hass.responses["input_boolean/list"] = lambda msg: [{"id": "ki_regeln_bad", "name": "ki_regeln_bad"}] if created else []

    def create(msg):
        created.append(msg)
        return {"id": "ki_regeln_bad", "name": msg["name"]}

    fake_hass.responses["input_boolean/create"] = create
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    try:
        ws = HaWs(client)
        assert await ws.ensure_helper("input_boolean", "ki_regeln_bad", icon="mdi:robot") == "input_boolean.ki_regeln_bad"
        assert await ws.ensure_helper("input_boolean", "ki_regeln_bad", icon="mdi:robot") == "input_boolean.ki_regeln_bad"
    finally:
        await client.close()
    assert len(created) == 1 and created[0]["icon"] == "mdi:robot"


async def test_delete_helper_by_name(fake_hass):
    deleted = []
    fake_hass.responses["input_boolean/list"] = [{"id": "abc", "name": "ki_regeln_bad"}]
    fake_hass.responses["input_boolean/delete"] = lambda msg: deleted.append(msg) or None
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    try:
        ws = HaWs(client)
        assert await ws.delete_helper("input_boolean", "ki_regeln_bad") is True
        assert await ws.delete_helper("input_boolean", "gibt_es_nicht") is False
    finally:
        await client.close()
    assert deleted[0]["input_boolean_id"] == "abc"


async def test_blueprints_schedules_and_dashboard(fake_hass):
    fake_hass.responses["blueprint/list"] = {"fabsss/ki_regel.yaml": {"metadata": {"name": "KI-Regel (V1.0)"}}}
    fake_hass.responses["schedule/list"] = [
        {"id": "heating_schedule_office", "name": "x",
         "monday": [{"from": "06:30:00", "to": "18:30:00"}], "tuesday": [{"from": "07:00:00", "to": "12:00:00"}]},
        {"id": "other", "name": "y", "monday": [{"from": "01:00:00", "to": "02:00:00"}]},
    ]
    saved = []
    fake_hass.responses["lovelace/dashboards/list"] = []
    fake_hass.responses["lovelace/dashboards/create"] = lambda msg: saved.append(("create", msg)) or {}
    fake_hass.responses["lovelace/config/save"] = lambda msg: saved.append(("save", msg)) or None
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    try:
        ws = HaWs(client)
        assert await ws.blueprint_paths() == {"fabsss/ki_regel.yaml"}
        assert await ws.schedule_flanks("heating_schedule_office") == {"06:30", "18:30", "07:00", "12:00"}
        assert await ws.schedule_flanks("gibt_es_nicht") == set()
        await ws.ensure_dashboard("ki-regeln", "KI-Regeln", "mdi:robot")
        await ws.save_dashboard("ki-regeln", {"views": []})
    finally:
        await client.close()
    kinds = [kind for kind, _ in saved]
    assert kinds == ["create", "save"]
    assert saved[0][1]["url_path"] == "ki-regeln" and saved[1][1]["config"] == {"views": []}


async def test_existing_dashboard_is_not_created_again(fake_hass):
    created = []
    fake_hass.responses["lovelace/dashboards/list"] = [{"id": "ki_regeln", "url_path": "ki-regeln"}]
    fake_hass.responses["lovelace/dashboards/create"] = lambda msg: created.append(msg) or {}
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    try:
        await HaWs(client).ensure_dashboard("ki-regeln", "KI-Regeln", "mdi:robot")
    finally:
        await client.close()
    assert created == []
