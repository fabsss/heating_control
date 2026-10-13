from __future__ import annotations

from dataclasses import dataclass

from .hass_ws import HassClient
from .models import EntityInfo


class LabelNotFound(Exception):
    pass


class LabelEmpty(Exception):
    pass


@dataclass(frozen=True)
class UserInfo:
    user_id: str
    person_entity: str
    name: str | None


async def fetch_labeled_entities(client: HassClient, label_name: str) -> list[EntityInfo]:
    labels = await client.command("config/label_registry/list")
    label_id = next((label["label_id"] for label in labels if label.get("name") == label_name), None)
    if label_id is None:
        raise LabelNotFound(
            f"Das Label '{label_name}' existiert in Home Assistant nicht. "
            "Lege es an und weise es den Entitäten zu (siehe README)."
        )
    entities = await client.command("config/entity_registry/list")
    devices = await client.command("config/device_registry/list")
    device_area = {device["id"]: device.get("area_id") for device in devices}
    found: list[EntityInfo] = []
    for entry in entities:
        if label_id not in (entry.get("labels") or []) or entry.get("disabled_by"):
            continue
        area = entry.get("area_id") or device_area.get(entry.get("device_id"))
        device_class = entry.get("device_class") or entry.get("original_device_class")
        found.append(EntityInfo(entry["entity_id"], device_class, area))
    if not found:
        raise LabelEmpty(f"Keine aktive Entität trägt das Label '{label_name}'.")
    return sorted(found, key=lambda info: info.entity_id)


async def fetch_users(client: HassClient) -> list[UserInfo]:
    users: list[UserInfo] = []
    for state in await client.command("get_states"):
        if not state["entity_id"].startswith("person."):
            continue
        attributes = state.get("attributes") or {}
        user_id = attributes.get("user_id")
        if user_id:
            users.append(UserInfo(user_id, state["entity_id"], attributes.get("friendly_name")))
    return users
