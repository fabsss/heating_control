from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Callable

from collector.db import Database
from collector.models import parse_utc, to_utc_iso, utcnow

from .config import ExecutionConfig
from .models import RuleEvent
from .mqtt import Message
from .outcomes import evaluate, rule_stats
from .store import ExecStore

log = logging.getLogger("execution")


class Service:
    """Dauerbetrieb: nimmt Meldungen der Regeln entgegen, wertet sie aus und veröffentlicht Kennzahlen."""

    def __init__(self, cfg: ExecutionConfig, db: Database, store: ExecStore, bus, now: Callable = utcnow):
        self.cfg, self.db, self.store, self.bus, self.now = cfg, db, store, bus, now

    def register(self) -> None:
        self.bus.on("homeai/rule/+/fired", self.on_fired)
        self.bus.on("homeai/rule/+/active/set", self.on_active_set)

    async def on_fired(self, message: Message) -> None:
        rule_id = message.topic.split("/")[2]
        deployment = self.store.get_deployment(rule_id)
        if deployment is None or deployment.state == "entfernt":
            return
        try:
            data = json.loads(message.payload)
            stamp = to_utc_iso(parse_utc(data["ts"]))
        except (ValueError, KeyError, TypeError):
            log.warning("Unbrauchbare Meldung auf %s: %s", message.topic, message.payload[:200])
            return
        event = RuleEvent(rule_id, stamp, str(data.get("mode") or "schatten"),
                          str(data.get("result") or "nicht_ausgefuehrt"), str(data.get("reason") or ""),
                          data.get("target") or {}, data.get("current") or {}, message.payload)
        if self.store.add_event(event):
            await self.publish_stats(rule_id)

    async def on_active_set(self, message: Message) -> None:
        rule_id = message.topic.split("/")[2]
        deployment = self.store.get_deployment(rule_id)
        if deployment is None or deployment.state == "entfernt":
            return
        state = {"ON": "aktiv", "OFF": "schatten"}.get(message.payload.strip().upper())
        if state is not None:
            self.store.set_deployment_state(rule_id, state, self.now())

    async def evaluate_pending(self) -> int:
        now = self.now()
        done: set[str] = set()
        count = 0
        for event in self.store.pending_events():
            deployment = self.store.get_deployment(event.rule_id)
            if deployment is None:
                continue
            ts = parse_utc(event.ts_utc)
            if now < ts + timedelta(minutes=self.cfg.eval_after_min):
                continue
            verdict = evaluate(self.db, event, deployment, self.cfg)
            if verdict is None:
                start, end = ts - timedelta(minutes=self.cfg.eval_before_min), ts + timedelta(minutes=self.cfg.eval_after_min)
                self.store.save_outcome(event.id, "nicht_gewertet", start, end)
            else:
                self.store.save_outcome(event.id, verdict.outcome, verdict.window_start, verdict.window_end,
                                        lage=verdict.lage, abstand_min=verdict.abstand_min, deviation=verdict.deviation)
            done.add(event.rule_id)
            count += 1
        for rule_id in done:
            await self.publish_stats(rule_id)
        return count

    async def publish_stats(self, rule_id: str) -> None:
        payload = rule_stats(self.store, rule_id, self.cfg).to_payload()
        await self.bus.publish(f"homeai/rule/{rule_id}/stats", json.dumps(payload, ensure_ascii=False), retain=True)

    async def publish_all_stats(self) -> None:
        for deployment in self.store.list_deployments():
            await self.publish_stats(deployment.rule_id)

    async def run_loop(self, stop: asyncio.Event, interval: float = 60.0) -> None:
        while not stop.is_set():
            try:
                await self.evaluate_pending()
            except Exception:
                log.exception("Auswertung fehlgeschlagen")
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except asyncio.TimeoutError:
                pass
