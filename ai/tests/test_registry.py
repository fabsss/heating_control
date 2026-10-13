import pytest

from collector.hass_ws import HassClient
from collector.registry import LabelEmpty, LabelNotFound, fetch_labeled_entities, fetch_users
from tests.fake_hass import set_registry


def entry(entity_id, labels=("ki_db_sync",), area_id=None, device_id=None,
          device_class=None, original_device_class=None, disabled_by=None):
    return {"entity_id": entity_id, "labels": list(labels), "area_id": area_id, "device_id": device_id,
            "device_class": device_class, "original_device_class": original_device_class,
            "disabled_by": disabled_by}


async def connect(fake) -> HassClient:
    client = HassClient(fake.url, "secret")
    await client.connect()
    return client


async def test_returns_only_labeled_enabled_entities_sorted(fake_hass):
    set_registry(fake_hass, [
        entry("sensor.b", area_id="buero", device_class="temperature"),
        entry("sensor.a", area_id="bad"),
        entry("sensor.other", labels=("andere",)),
        entry("sensor.off", disabled_by="user"),
    ])
    client = await connect(fake_hass)
    try:
        infos = await fetch_labeled_entities(client, "KI_DB_Sync")
    finally:
        await client.close()
    assert [(i.entity_id, i.area, i.device_class) for i in infos] == [
        ("sensor.a", "bad", None),
        ("sensor.b", "buero", "temperature"),
    ]


async def test_area_falls_back_to_device_area_and_device_class_to_original(fake_hass):
    set_registry(
        fake_hass,
        [entry("sensor.x", device_id="dev1", original_device_class="humidity")],
        devices=[{"id": "dev1", "area_id": "wohnzimmer"}],
    )
    client = await connect(fake_hass)
    try:
        (info,) = await fetch_labeled_entities(client, "KI_DB_Sync")
    finally:
        await client.close()
    assert (info.area, info.device_class) == ("wohnzimmer", "humidity")


async def test_entity_area_beats_device_area(fake_hass):
    set_registry(
        fake_hass,
        [entry("sensor.x", area_id="bad", device_id="dev1")],
        devices=[{"id": "dev1", "area_id": "wohnzimmer"}],
    )
    client = await connect(fake_hass)
    try:
        (info,) = await fetch_labeled_entities(client, "KI_DB_Sync")
    finally:
        await client.close()
    assert info.area == "bad"


async def test_missing_label_raises(fake_hass):
    set_registry(fake_hass, [entry("sensor.x")], labels=[])
    client = await connect(fake_hass)
    try:
        with pytest.raises(LabelNotFound, match="KI_DB_Sync"):
            await fetch_labeled_entities(client, "KI_DB_Sync")
    finally:
        await client.close()


async def test_label_without_entities_raises(fake_hass):
    set_registry(fake_hass, [entry("sensor.x", labels=())])
    client = await connect(fake_hass)
    try:
        with pytest.raises(LabelEmpty, match="KI_DB_Sync"):
            await fetch_labeled_entities(client, "KI_DB_Sync")
    finally:
        await client.close()


async def test_fetch_users_reads_user_ids_from_person_entities(fake_hass):
    fake_hass.responses["get_states"] = [
        {"entity_id": "person.fabian", "attributes": {"user_id": "u1", "friendly_name": "Fabian"}},
        {"entity_id": "person.gast", "attributes": {"friendly_name": "Gast"}},
        {"entity_id": "sensor.x", "attributes": {"user_id": "nein"}},
    ]
    client = await connect(fake_hass)
    try:
        users = await fetch_users(client)
    finally:
        await client.close()
    assert [(u.user_id, u.person_entity, u.name) for u in users] == [("u1", "person.fabian", "Fabian")]
