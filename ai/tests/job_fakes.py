from __future__ import annotations

import asyncio

from tests.ha_template import utc


class Clock:
    def __init__(self):
        self.value = utc(2026, 10, 15, 12, 0)

    def __call__(self):
        return self.value


class FakeExecutor:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.result: tuple[int, str] = (0, "ausgabe")
        self.gate: asyncio.Event | None = None

    async def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        if self.gate is not None:
            await self.gate.wait()
        return self.result


class FakeActions:
    def __init__(self):
        self.deployed: list[int] = []
        self.undeployed: list[str] = []
        self.refreshed = 0

    async def status(self) -> str:
        return "Status: 2 Regeln"

    async def deploy(self, candidate_id: int) -> str:
        self.deployed.append(candidate_id)
        return f"Regel r{candidate_id} angelegt"

    async def undeploy(self, rule_id: str) -> str:
        self.undeployed.append(rule_id)
        return f"Regel {rule_id} entfernt"

    async def check(self) -> str:
        return "OK    alles"

    async def refresh_dashboard(self) -> None:
        self.refreshed += 1
