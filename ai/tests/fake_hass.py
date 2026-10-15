from __future__ import annotations

import json
from typing import Any

from aiohttp import web

DEFAULT_LABELS = [{"label_id": "ki_db_sync", "name": "KI_DB_Sync"}]


class FakeHass:
    """Kleine Attrappe der Home-Assistant-WebSocket-API für Tests."""

    def __init__(self, token: str = "secret"):
        self.token = token
        self.url = ""
        self.responses: dict[str, Any] = {}
        self.events: list[dict] = []
        self.events_by_type: dict[str, list[dict]] = {}
        self.batch_events = False
        self.close_after_events = False
        self.close_after_command: str | None = None
        self.received: list[dict] = []

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        auth = await ws.receive_json()
        if auth.get("access_token") != self.token:
            await ws.send_json({"type": "auth_invalid", "message": "bad token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})
        async for raw in ws:
            msg = json.loads(raw.data)
            self.received.append(msg)
            if msg["type"] == "subscribe_events":
                await ws.send_json({"id": msg["id"], "type": "result", "success": True, "result": None})
                event_type = msg.get("event_type")
                events = self.events if event_type in (None, "state_changed") else self.events_by_type.get(event_type, [])
                if self.batch_events and events:
                    await ws.send_json(
                        [{"id": msg["id"], "type": "event", "event": event} for event in events]
                    )
                else:
                    for event in events:
                        await ws.send_json({"id": msg["id"], "type": "event", "event": event})
                if self.close_after_events:
                    await ws.close()
                continue
            if msg["type"] not in self.responses:
                await ws.send_json({"id": msg["id"], "type": "result", "success": False,
                                    "error": {"code": "unknown_command", "message": msg["type"]}})
                continue
            result = self.responses[msg["type"]]
            if callable(result):
                result = result(msg)
            await ws.send_json({"id": msg["id"], "type": "result", "success": True, "result": result})
            if self.close_after_command == msg["type"]:
                await ws.close()
        return ws


def set_registry(fake: FakeHass, entities: list[dict], labels: list[dict] | None = None,
                 devices: list[dict] | None = None) -> None:
    fake.responses["config/label_registry/list"] = DEFAULT_LABELS if labels is None else labels
    fake.responses["config/entity_registry/list"] = entities
    fake.responses["config/device_registry/list"] = devices or []
