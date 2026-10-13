from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

import aiohttp


class HassConnectionError(Exception):
    pass


class HassAuthError(Exception):
    pass


class HassCommandError(Exception):
    pass


class HassClient:
    """Minimaler Client für die Home-Assistant-WebSocket-API."""

    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._subs: dict[int, asyncio.Queue] = {}
        self._last_message = time.monotonic()

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(self.url, heartbeat=30, max_msg_size=0)
            hello = await self._ws.receive_json()
            if hello.get("type") != "auth_required":
                raise HassConnectionError(f"unerwartete Begrüßung: {hello}")
            await self._ws.send_json({"type": "auth", "access_token": self.token})
            answer = await self._ws.receive_json()
        except HassConnectionError:
            await self.close()
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError) as exc:
            await self.close()
            raise HassConnectionError(str(exc)) from exc
        if answer.get("type") == "auth_invalid":
            await self.close()
            raise HassAuthError(answer.get("message", "auth_invalid"))
        if answer.get("type") != "auth_ok":
            await self.close()
            raise HassConnectionError(f"unerwartete Antwort: {answer}")
        self._last_message = time.monotonic()
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()
            self._session = None

    def seconds_since_last_message(self) -> float:
        return time.monotonic() - self._last_message

    async def command(self, type_: str, **payload: Any) -> Any:
        message = await self._request({"type": type_, **payload})
        if not message.get("success"):
            error = message.get("error") or {}
            raise HassCommandError(f"{type_}: {error.get('code')} {error.get('message')}")
        return message.get("result")

    async def subscribe(self, event_type: str) -> AsyncIterator[dict]:
        if self._ws is None or self._ws.closed:
            raise HassConnectionError("nicht verbunden")
        queue: asyncio.Queue = asyncio.Queue()
        message_id = self._allocate_id()
        self._subs[message_id] = queue
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self._send({"id": message_id, "type": "subscribe_events", "event_type": event_type})
        answer = await future
        if not answer.get("success"):
            raise HassCommandError(f"subscribe_events: {answer.get('error')}")
        return self._iterate(queue)

    async def _iterate(self, queue: asyncio.Queue) -> AsyncIterator[dict]:
        while True:
            item = await queue.get()
            if item is None:
                raise HassConnectionError("Verbindung geschlossen")
            yield item

    def _allocate_id(self) -> int:
        message_id = self._next_id
        self._next_id += 1
        return message_id

    async def _send(self, payload: dict) -> None:
        if self._ws is None or self._ws.closed:
            raise HassConnectionError("nicht verbunden")
        try:
            await self._ws.send_json(payload)
        except (aiohttp.ClientError, ConnectionResetError) as exc:
            raise HassConnectionError(str(exc)) from exc

    async def _request(self, payload: dict) -> dict:
        if self._ws is None or self._ws.closed:
            raise HassConnectionError("nicht verbunden")
        message_id = self._allocate_id()
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            await self._send({"id": message_id, **payload})
        except HassConnectionError:
            self._pending.pop(message_id, None)
            raise
        return await future

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    continue
                self._last_message = time.monotonic()
                data = json.loads(raw.data)
                for message in data if isinstance(data, list) else [data]:
                    self._dispatch(message)
        except (aiohttp.ClientError, ValueError):
            pass
        finally:
            error = HassConnectionError("Verbindung geschlossen")
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            self._pending.clear()
            for queue in self._subs.values():
                queue.put_nowait(None)

    def _dispatch(self, message: dict) -> None:
        kind, message_id = message.get("type"), message.get("id")
        if kind == "result":
            future = self._pending.pop(message_id, None)
            if future is not None and not future.done():
                future.set_result(message)
        elif kind == "event":
            queue = self._subs.get(message_id)
            if queue is not None:
                queue.put_nowait(message.get("event"))
