from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import aiohttp

from collector.hass_ws import HassClient

from .helpers import slug


class HaError(RuntimeError):
    pass


def rest_base(ws_url: str) -> str:
    parsed = urlparse(ws_url)
    return f"{'https' if parsed.scheme == 'wss' else 'http'}://{parsed.netloc}"


class HaRest:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._session is None:
            self._session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {self.token}"})
        try:
            async with self._session.request(method, self.base_url + path, **kwargs) as response:
                text = await response.text()
                if response.status == 404:
                    return None
                if response.status >= 400:
                    raise HaError(f"{method} {path}: {response.status} {text[:200]}")
                try:
                    return json.loads(text) if text else {}
                except ValueError:
                    return {}
        except aiohttp.ClientError as exc:
            raise HaError(f"{method} {path}: {exc}") from exc

    async def get_state(self, entity_id: str) -> dict | None:
        return await self._request("GET", f"/api/states/{entity_id}")

    async def list_states(self) -> list[dict]:
        return await self._request("GET", "/api/states") or []

    async def automation_config(self, config_id: str) -> dict | None:
        return await self._request("GET", f"/api/config/automation/config/{config_id}")

    async def save_automation(self, config_id: str, config: dict) -> None:
        await self._request("POST", f"/api/config/automation/config/{config_id}", json=config)

    async def delete_automation(self, config_id: str) -> None:
        await self._request("DELETE", f"/api/config/automation/config/{config_id}")

    async def find_automation_entity(self, config_id: str, *, retries: int = 10, delay: float = 0.5) -> str | None:
        for attempt in range(retries):
            for state in await self.list_states():
                if state["entity_id"].startswith("automation.") and state["attributes"].get("id") == config_id:
                    return state["entity_id"]
            if attempt + 1 < retries:
                await asyncio.sleep(delay)
        return None


class HaWs:
    def __init__(self, client: HassClient):
        self.client = client

    async def _helpers(self, domain: str) -> dict[str, dict]:
        items = await self.client.command(f"{domain}/list")
        return {item["name"]: item for item in items or []}

    async def ensure_helper(self, domain: str, name: str, **options: Any) -> str:
        if name not in await self._helpers(domain):
            await self.client.command(f"{domain}/create", name=name, **options)
        return f"{domain}.{slug(name)}"

    async def delete_helper(self, domain: str, name: str) -> bool:
        item = (await self._helpers(domain)).get(name)
        if item is None:
            return False
        await self.client.command(f"{domain}/delete", **{f"{domain}_id": item["id"]})
        return True

    async def blueprint_paths(self) -> set[str]:
        return set(await self.client.command("blueprint/list", domain="automation") or {})

    async def schedule_flanks(self, schedule_id: str) -> set[str]:
        flanks: set[str] = set()
        for item in await self.client.command("schedule/list") or []:
            if item.get("id") != schedule_id:
                continue
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
                for block in item.get(day) or []:
                    flanks.update(str(block[key])[:5] for key in ("from", "to") if block.get(key))
        return flanks

    async def ensure_dashboard(self, url_path: str, title: str, icon: str) -> None:
        existing = await self.client.command("lovelace/dashboards/list") or []
        if any(item.get("url_path") == url_path for item in existing):
            return
        await self.client.command("lovelace/dashboards/create", url_path=url_path, title=title, icon=icon,
                                  show_in_sidebar=True, require_admin=False)

    async def save_dashboard(self, url_path: str, config: dict) -> None:
        await self.client.command("lovelace/config/save", url_path=url_path, config=config)


@asynccontextmanager
async def ha_connection(url: str, token: str):
    rest = HaRest(rest_base(url), token)
    client = HassClient(url, token)
    await client.connect()
    try:
        yield rest, HaWs(client)
    finally:
        await client.close()
        await rest.close()
