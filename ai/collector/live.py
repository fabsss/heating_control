from __future__ import annotations

import asyncio
import inspect
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import aiohttp

from .config import Config
from .db import Database
from .hass_ws import HassAuthError, HassClient, HassConnectionError
from .models import Change, parse_utc, utcnow
from .pipeline import Pipeline
from .registry import LabelEmpty, LabelNotFound, fetch_labeled_entities, fetch_users
from .rules import resolve_rule

log = logging.getLogger("collector")

ROOM_BOUND_KINDS = {"climate", "presence", "contact", "env"}
HISTORY_DAYS = 180
HEALTH_MAX_AGE_S = 600
HEARTBEAT_INTERVAL_S = 30.0
BACKOFF_START = 1.0
BACKOFF_MAX = 60.0


def change_from_event(event: dict[str, Any]) -> Change | None:
    new_state = (event.get("data") or {}).get("new_state")
    if not new_state:
        return None
    context = new_state.get("context") or {}
    return Change(
        ts=parse_utc(new_state["last_updated"]),
        entity_id=new_state["entity_id"],
        state=new_state.get("state"),
        attrs=new_state.get("attributes") or {},
        context_id=context.get("id"),
        context_user_id=context.get("user_id"),
        context_parent_id=context.get("parent_id"),
    )


def history_to_changes(entity_id: str, states: list[dict[str, Any]]) -> list[Change]:
    changes: list[Change] = []
    for item in states:
        stamp = item.get("lu", item.get("lc"))
        if stamp is None or item.get("s") is None:
            continue
        changes.append(Change(datetime.fromtimestamp(float(stamp), tz=timezone.utc),
                              entity_id, item["s"], item.get("a") or {}))
    return changes


def seconds_until(hhmm: str, tz_name: str, now: datetime) -> float:
    local = now.astimezone(ZoneInfo(tz_name))
    hour, minute = (int(part) for part in hhmm.split(":"))
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return (target - local).total_seconds()


class Collector:
    def __init__(self, cfg: Config, db: Database, client_factory: Callable[[], HassClient], *,
                 health_file: Path | None = None, now: Callable[[], datetime] = utcnow,
                 sleep: Callable[[float], Any] = asyncio.sleep):
        self.cfg = cfg
        self.db = db
        self.pipeline = Pipeline(db)
        self._client_factory = client_factory
        self._health_file = health_file
        self._now = now
        self._sleep = sleep
        self._stop = asyncio.Event()
        self._first_sync = True

    def stop(self) -> None:
        self._stop.set()

    # --- Registry und Lücken ---------------------------------------------------

    async def sync_registry(self, client: HassClient) -> None:
        try:
            infos = await fetch_labeled_entities(client, self.cfg.label)
        except (LabelNotFound, LabelEmpty):
            if self._first_sync:
                raise
            log.exception("Label-Abgleich fehlgeschlagen, bisherige Entitäten bleiben aktiv")
            return
        now = self._now()
        rules = {}
        for info in infos:
            rule = resolve_rule(info, self.cfg)
            rules[info.entity_id] = rule
            if info.area is None and rule.kind in ROOM_BOUND_KINDS:
                log.warning("%s hat keinen Bereich in Home Assistant, die Raumzuordnung fehlt", info.entity_id)
            self.db.upsert_entity(rule, info.area, now)
            self.db.sync_room(info.entity_id, info.area, rule.kind, now)
        for entity_id in self.db.deactivate_missing(set(rules), now):
            log.info("%s hat das Label verloren, Aufzeichnung gestoppt (Daten bleiben)", entity_id)
        self.pipeline.set_rules(rules)
        for user in await fetch_users(client):
            self.db.upsert_user(user.user_id, user.person_entity, user.name)
        log.info("Registry-Abgleich: %d Entitäten mit Label %s", len(rules), self.cfg.label)
        self._first_sync = False

    async def gap_fill(self, client: HassClient) -> int:
        now = self._now()
        floor = now - timedelta(days=HISTORY_DAYS)
        total = 0
        for entity_id in sorted(self.pipeline.rules):
            start = max(self.db.last_ts(entity_id) or floor, floor)
            result = await client.command(
                "history/history_during_period",
                start_time=start.isoformat(),
                end_time=now.isoformat(),
                entity_ids=[entity_id],
                include_start_time_state=True,
                significant_changes_only=False,
                minimal_response=False,
                no_attributes=False,
            )
            changes = history_to_changes(entity_id, (result or {}).get(entity_id, []))
            for change in changes:
                self.pipeline.handle(change, source="gap:history_api")
            total += len(changes)
        self.pipeline.flush()
        return total

    # --- Sitzung ---------------------------------------------------------------------

    async def run_session(self, client: HassClient) -> None:
        await client.connect()
        self._touch_health()
        await self.sync_registry(client)
        stream = await client.subscribe("state_changed")   # vor dem Lückenabgleich, damit nichts fehlt
        await self.gap_fill(client)
        tasks = [
            asyncio.create_task(self._consume(stream)),
            asyncio.create_task(self._periodic(self.cfg.flush_interval_s, self._flush)),
            asyncio.create_task(self._periodic(1.0, self._tick)),
            asyncio.create_task(self._periodic(self.cfg.registry_sync_interval_s, self.sync_registry, client)),
            asyncio.create_task(self._periodic(HEARTBEAT_INTERVAL_S, self._heartbeat, client)),
            asyncio.create_task(self._backup_loop()),
            asyncio.create_task(self._stop.wait()),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._flush()

    async def run(self) -> None:
        backoff = BACKOFF_START
        while not self._stop.is_set():
            client = self._client_factory()
            started = time.monotonic()
            try:
                await self.run_session(client)
            except (HassAuthError, LabelNotFound, LabelEmpty):
                raise
            except (HassConnectionError, aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
                log.warning("Verbindung zu Home Assistant verloren: %s", exc)
            finally:
                await client.close()
            if self._stop.is_set():
                break
            if time.monotonic() - started > 60:
                backoff = BACKOFF_START
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, BACKOFF_MAX)

    # --- Hilfsaufgaben ---------------------------------------------------------------

    async def _consume(self, stream) -> None:
        async for event in stream:
            change = change_from_event(event)
            if change is not None:
                self.pipeline.handle(change)

    async def _periodic(self, interval: float, function: Callable[..., Any], *args: Any) -> None:
        while True:
            await self._sleep(interval)
            result = function(*args)
            if inspect.isawaitable(result):
                await result

    def _flush(self) -> None:
        try:
            self.pipeline.flush()
        except sqlite3.Error:
            log.exception("Schreiben in die Datenbank fehlgeschlagen, Puffer bleibt erhalten")

    def _tick(self) -> None:
        self.pipeline.tick(self._now())

    def _heartbeat(self, client: HassClient) -> None:
        if client.seconds_since_last_message() < HEALTH_MAX_AGE_S:
            self._touch_health()

    def _touch_health(self) -> None:
        if self._health_file is not None:
            self._health_file.parent.mkdir(parents=True, exist_ok=True)
            self._health_file.touch()

    async def _backup_loop(self) -> None:
        if not self.cfg.backup_path:
            await asyncio.Event().wait()          # nicht konfiguriert: nie beenden
        while True:
            await self._sleep(seconds_until(self.cfg.backup_time, self.cfg.timezone, self._now()))
            try:
                self.db.backup_to(self.cfg.backup_path)
                log.info("Datenbank gesichert nach %s", self.cfg.backup_path)
            except Exception:
                log.exception("Sicherung der Datenbank fehlgeschlagen")
