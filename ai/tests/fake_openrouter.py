from __future__ import annotations

from aiohttp import web


class FakeOpenRouter:
    """Kleine Attrappe der OpenRouter-Chat-Schnittstelle für Tests."""

    def __init__(self):
        self.url = ""
        self.responses: list[dict] = []
        self.requests: list[dict] = []

    async def handler(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.requests.append({"payload": payload, "auth": request.headers.get("Authorization")})
        item = self.responses.pop(0) if self.responses else {"content": '{"candidates": []}'}
        status = item.get("status", 200)
        if status != 200:
            return web.json_response({"error": {"message": item.get("message", "Fehler")}}, status=status)
        usage = {"prompt_tokens": item.get("tokens_in", 100), "completion_tokens": item.get("tokens_out", 50)}
        if item.get("cost", 0.01) is not None:
            usage["cost"] = item.get("cost", 0.01)
        return web.json_response({
            "choices": [{"message": {"role": "assistant", "content": item["content"]}}],
            "usage": usage,
        })
