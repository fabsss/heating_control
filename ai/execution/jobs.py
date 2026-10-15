from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Protocol
from zoneinfo import ZoneInfo

from analysis.candidates import describe
from analysis.config import AnalysisConfig
from analysis.pseudonym import build_pseudonyms
from analysis.store import Store as AnalysisStore
from collector.db import Database
from collector.models import to_utc_iso, utcnow

from .config import ExecutionConfig
from .controls import JOB_LABELS, MODES, NO_SELECTION
from .mqtt import Message
from .store import ExecStore

log = logging.getLogger("execution")

FENCE = "`" * 3
REFRESH_AFTER = ("llm_run", "deploy", "undeploy")
CLI_ARGS: dict[str, Callable[[dict[str, str]], list[str]]] = {
    "signals": lambda sel: ["signals"],
    "scan": lambda sel: ["scan"],
    "llm_dry": lambda sel: ["run", "--mode", sel["mode"], "--dry-run"],
    "llm_run": lambda sel: ["run", "--mode", sel["mode"]],
    "candidates": lambda sel: ["list", "--status", "bestanden"],
    "latest": lambda sel: ["list", "--latest"],
    "compare": lambda sel: ["compare"],
}


class JobActions(Protocol):
    async def status(self) -> str: ...
    async def deploy(self, candidate_id: int) -> str: ...
    async def undeploy(self, rule_id: str) -> str: ...
    async def check(self) -> str: ...
    async def refresh_dashboard(self) -> None: ...


class SubprocessExecutor:
    """Führt `python -m analysis …` als eigenen Prozess aus (eigene Umgebung, kein gemeinsamer Zustand)."""

    def __init__(self, python: str = sys.executable, module: str = "analysis", timeout_s: float = 1800.0):
        self.python, self.module, self.timeout_s = python, module, timeout_s

    async def __call__(self, argv: list[str]) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            self.python, "-m", self.module, *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), self.timeout_s)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return 124, "Zeitüberschreitung: der Auftrag wurde abgebrochen."
        return process.returncode or 0, output.decode("utf-8", "replace")


class JobRunner:
    def __init__(self, cfg: ExecutionConfig, store: ExecStore, bus, executor, actions: JobActions, *,
                 candidates: Callable[[], list[str]], rules: Callable[[], list[str]],
                 refresh: Callable[[], Awaitable[None]], now: Callable[[], datetime] = utcnow):
        self.cfg, self.store, self.bus, self.executor, self.actions = cfg, store, bus, executor, actions
        self.candidates, self.rules, self.refresh, self.now = candidates, rules, refresh, now
        self.selection = {"mode": "scan", "candidate": NO_SELECTION, "rule": NO_SELECTION}
        self.running: str | None = None
        self.armed_until: datetime | None = None
        self._tasks: set[asyncio.Task] = set()

    def register(self) -> None:
        self.bus.on("homeai/cmd/+", self.on_command)
        self.bus.on("homeai/sel/+/set", self.on_select)

    async def start(self) -> None:
        await self._publish_state("idle", "")
        await self._publish_arm(False)
        for what, value in self.selection.items():
            await self.bus.publish(f"homeai/sel/{what}/state", value, retain=True)

    async def wait(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
            await asyncio.sleep(0)

    # --- Eingänge ----------------------------------------------------------------------------

    async def on_command(self, message: Message) -> None:
        name, payload = message.topic.split("/")[2], message.payload.strip()
        if name == "llm_arm":
            await (self.arm() if payload.upper() == "ON" else self.disarm())
            return
        if name not in JOB_LABELS or payload != "PRESS":
            return
        problem = self._rejection(name)
        if problem:
            await self._publish_result(name, "abgelehnt", problem)
            return
        self.running = name
        task = asyncio.create_task(self._run(name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def on_select(self, message: Message) -> None:
        what = message.topic.split("/")[2]
        allowed = {"mode": list(MODES), "candidate": [*self.candidates(), NO_SELECTION],
                   "rule": [*self.rules(), NO_SELECTION]}.get(what)
        if allowed is None or message.payload not in allowed:
            log.warning("Ungültige Auswahl %r für %s", message.payload, what)
            return
        self.selection[what] = message.payload
        await self.bus.publish(f"homeai/sel/{what}/state", message.payload, retain=True)

    async def tick(self) -> None:
        if self.armed_until is not None and self.now() >= self.armed_until:
            await self.disarm()

    async def arm(self) -> None:
        self.armed_until = self.now() + timedelta(seconds=self.cfg.arm_seconds)
        await self._publish_arm(True)

    async def disarm(self) -> None:
        self.armed_until = None
        await self._publish_arm(False)

    # --- Ausführung -----------------------------------------------------------------------------

    def _runs_today(self) -> int:
        local = self.now().astimezone(ZoneInfo(self.cfg.timezone))
        start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        return self.store.count_jobs_since("llm_run", start)

    def _rejection(self, name: str) -> str | None:
        if self.running:
            return f"Es läuft bereits ein Auftrag ({self.running}); bitte warten."
        if name == "llm_run":
            if self.armed_until is None or self.now() >= self.armed_until:
                return (f"Kosten nicht freigegeben: zuerst „Kosten freigeben“ einschalten "
                        f"(gilt {self.cfg.arm_seconds} Sekunden).")
            if self._runs_today() >= self.cfg.llm_runs_per_day:
                return f"Tageslimit erreicht: höchstens {self.cfg.llm_runs_per_day} Läufe mit dem Modell je Tag."
        return None

    def _selected(self, what: str, label: str) -> str:
        value = self.selection[what]
        if value == NO_SELECTION:
            raise ValueError(f"Kein {label} gewählt")
        return value.split(":", 1)[0].strip()

    async def _run(self, name: str) -> None:
        try:
            job_id = self.store.start_job(name, dict(self.selection), self.now())
            await self._publish_state("laeuft", name)
            try:
                state, text = await self._execute(name)
            except Exception as exc:                     # Fehler eines Auftrags dürfen den Dienst nicht beenden
                log.exception("Auftrag %s fehlgeschlagen", name)
                state, text = "fehler", f"{type(exc).__name__}: {exc}"
            text = self._shorten(text)
            self.store.finish_job(job_id, state, text[:2000], self.now())
            if name == "llm_run":
                await self.disarm()
            await self._publish_result(name, state, text)
            await self._publish_state("idle", name, state)
            if name in REFRESH_AFTER:
                await self.refresh()
        finally:
            self.running = None

    async def _execute(self, name: str) -> tuple[str, str]:
        if name == "status":
            return "ok", await self.actions.status()
        if name == "check":
            return "ok", await self.actions.check()
        if name == "deploy":
            return "ok", await self.actions.deploy(int(self._selected("candidate", "Kandidat")))
        if name == "undeploy":
            return "ok", await self.actions.undeploy(self._selected("rule", "Regel"))
        code, output = await self.executor(CLI_ARGS[name](self.selection))
        return ("ok" if code == 0 else "fehler"), output

    def _shorten(self, text: str) -> str:
        limit = self.cfg.job_output_limit
        return text if len(text) <= limit else text[:limit] + "\n… (gekürzt)"

    # --- Ausgänge ---------------------------------------------------------------------------------

    async def _publish_state(self, state: str, job: str, message: str = "") -> None:
        payload = {"state": state, "job": job, "ts": to_utc_iso(self.now()), "message": message}
        await self.bus.publish("homeai/job/state", json.dumps(payload, ensure_ascii=False), retain=True)

    async def _publish_result(self, name: str, state: str, text: str) -> None:
        payload = {"title": f"{JOB_LABELS[name]}: {state}", "markdown": f"{FENCE}\n{text}\n{FENCE}",
                   "ts": to_utc_iso(self.now())}
        await self.bus.publish("homeai/job/result", json.dumps(payload, ensure_ascii=False), retain=True)

    async def _publish_arm(self, on: bool) -> None:
        await self.bus.publish("homeai/state/llm_arm", "ON" if on else "OFF", retain=True)


def candidate_options(db: Database, astore: AnalysisStore, acfg: AnalysisConfig, store: ExecStore) -> list[str]:
    deployed = {item.candidate_id for item in store.list_deployments()}
    pseudo, signals = build_pseudonyms(db), astore.signal_descriptions()
    options = []
    for candidate in astore.list_candidates(status="bestanden"):
        if candidate.id in deployed:
            continue
        room, when, action = describe(candidate, acfg, pseudo, signals)
        options.append(f"{candidate.id}: {room} {when} -> {action}"[:200])
    return options


def rule_options(store: ExecStore) -> list[str]:
    return [f"{item.rule_id}: {item.description}"[:200] for item in store.list_deployments()]


def status_text(db: Database, store: ExecStore) -> str:
    deployments = store.list_deployments()
    active = sum(1 for item in deployments if item.state == "aktiv")
    entities = db.conn.execute("SELECT COUNT(*) FROM entities WHERE active = 1").fetchone()[0]
    last = db.conn.execute("SELECT MAX(ts_utc) FROM state_changes WHERE source = 'live'").fetchone()[0]
    lines = [f"Regeln: {len(deployments)} angelegt, {active} aktiv, {len(deployments) - active} im Schatten",
             f"Aufgezeichnete Entitäten: {entities}",
             f"Letztes aufgezeichnetes Ereignis: {last or 'noch keines'}"]
    jobs = store.recent_jobs(5)
    if jobs:
        lines.append("Letzte Aufträge:")
        lines += [f"  {job['started_at'][:19]}Z {job['name']}: {job['state']}" for job in jobs]
    return "\n".join(lines)
