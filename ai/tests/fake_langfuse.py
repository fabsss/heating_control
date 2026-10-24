from __future__ import annotations

import json

from aiohttp import web


def _value(raw: dict):
    (kind, value), = raw.items()
    return int(value) if kind == "intValue" else value


class FakeLangfuse:
    """Kleine Attrappe von Langfuse v4: OTLP-Annahme, Scores und Prompt-Schnittstelle."""

    def __init__(self):
        self.url = ""
        self.requests: list[dict] = []           # OTLP-Körper
        self.headers: list[dict] = []
        self.scores: list[dict] = []
        self.prompts: dict[str, dict] = {}       # Name -> {"prompt": ..., "version": n, "type": "text"}
        self.created: list[dict] = []
        self.otlp_status = 200
        self.prompts_status = 200

    async def otlp(self, request: web.Request) -> web.Response:
        self.headers.append(dict(request.headers))
        self.requests.append(await request.json())
        return web.json_response({}, status=self.otlp_status)

    async def score(self, request: web.Request) -> web.Response:
        self.scores.append(await request.json())
        return web.json_response({"id": "s"}, status=200)

    async def get_prompt(self, request: web.Request) -> web.Response:
        if self.prompts_status != 200:
            return web.json_response({}, status=self.prompts_status)
        name = request.match_info["name"]
        if name not in self.prompts:
            return web.json_response({}, status=404)
        return web.json_response({"name": name, **self.prompts[name]})

    async def create_prompt(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.created.append(body)
        self.prompts[body["name"]] = {"type": body["type"], "prompt": body["prompt"], "version": 1}
        return web.json_response(body, status=201)

    def spans(self) -> list[dict]:
        """Alle gesendeten Spans mit Attributen als Wörterbuch (``attrs``) und Status."""
        out = []
        for body in self.requests:
            for resource in body["resourceSpans"]:
                for scope in resource["scopeSpans"]:
                    for span in scope["spans"]:
                        attrs = {a["key"]: _value(a["value"]) for a in span["attributes"]}
                        out.append({**span, "attrs": attrs})
        return out

    def observations(self, kind: str | None = None) -> list[dict]:
        found = [s for s in self.spans() if s.get("parentSpanId")]
        return [s for s in found if kind is None or s["attrs"].get("langfuse.observation.type") == kind]

    def root(self) -> dict:
        return next(s for s in self.spans() if not s.get("parentSpanId"))

    @staticmethod
    def parsed(span: dict, key: str):
        raw = span["attrs"].get(key)
        return json.loads(raw) if isinstance(raw, str) and raw[:1] in "{[" else raw
