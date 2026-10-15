from __future__ import annotations

from aiohttp import web

from execution.helpers import slug


class FakeHaRest:
    """Kleine Attrappe der Home-Assistant-REST-API (Zustände und Automationen)."""

    def __init__(self, token: str = "secret"):
        self.token = token
        self.base = ""
        self.automations: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        self.fail_save = False
        self.requests: list[tuple[str, str]] = []

    def add_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/states", self.list_states)
        app.router.add_get("/api/states/{entity_id}", self.get_state)
        app.router.add_get("/api/config/automation/config/{config_id}", self.get_config)
        app.router.add_post("/api/config/automation/config/{config_id}", self.save_config)
        app.router.add_delete("/api/config/automation/config/{config_id}", self.delete_config)

    def app(self) -> web.Application:
        app = web.Application()
        self.add_routes(app)
        return app

    def _check(self, request: web.Request) -> web.Response | None:
        self.requests.append((request.method, request.path))
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return web.Response(status=401, text="unauthorized")
        return None

    def set_state(self, entity_id: str, state: str, **attributes) -> None:
        self.states[entity_id] = {"entity_id": entity_id, "state": state, "attributes": attributes}

    async def list_states(self, request: web.Request) -> web.Response:
        denied = self._check(request)
        if denied is not None:
            return denied
        return web.json_response(list(self.states.values()))

    async def get_state(self, request: web.Request) -> web.Response:
        denied = self._check(request)
        if denied is not None:
            return denied
        state = self.states.get(request.match_info["entity_id"])
        return web.json_response(state) if state else web.Response(status=404, text="not found")

    async def get_config(self, request: web.Request) -> web.Response:
        denied = self._check(request)
        if denied is not None:
            return denied
        config = self.automations.get(request.match_info["config_id"])
        return web.json_response(config) if config is not None else web.Response(status=404, text="not found")

    async def save_config(self, request: web.Request) -> web.Response:
        denied = self._check(request)
        if denied is not None:
            return denied
        if self.fail_save:
            return web.Response(status=500, text="boom")
        config_id = request.match_info["config_id"]
        config = await request.json()
        self.automations[config_id] = config
        entity_id = f"automation.{slug(config.get('alias', config_id))}"
        self.set_state(entity_id, "on", id=config_id, friendly_name=config.get("alias"))
        return web.json_response({"result": "ok"})

    async def delete_config(self, request: web.Request) -> web.Response:
        denied = self._check(request)
        if denied is not None:
            return denied
        config_id = request.match_info["config_id"]
        if config_id not in self.automations:
            return web.Response(status=404, text="not found")
        del self.automations[config_id]
        for entity_id in [key for key, state in self.states.items() if state["attributes"].get("id") == config_id]:
            del self.states[entity_id]
        return web.json_response({"result": "ok"})
