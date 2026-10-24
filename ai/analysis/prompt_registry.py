"""Prompt-Verwaltung über Langfuse, die Prompts im Code sind die Rückfallebene.

Die Prompts bleiben im Code (``prompts.py``). Ist Langfuse eingerichtet, wird bei jedem Lauf die Version mit
dem Label ``production`` geladen. So lassen sich Prompts in der Langfuse-Oberfläche ändern und versionieren,
und jede Generation verweist auf die benutzte Version. Bei jedem Problem (Langfuse nicht erreichbar, Prompt
fehlt, Variable gelöscht) gilt der Prompt aus dem Code.

Variablen stehen in Langfuse als ``{{name}}``. Ersetzt werden nur die bekannten Variablen, die JSON-Klammern
im Antwortformat bleiben unberührt. Ein Lauf ist ein kurzlebiger Auftrag, ein Zwischenspeicher lohnt nicht.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

from . import langfuse

log = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=3)
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_seeded = False


@dataclass(frozen=True)
class PromptSpec:
    key: str                       # "system", "weekly", "scan", "clusters"
    name: str                      # Name in Langfuse
    default: str                   # Prompt aus dem Code, Variablen als {name}
    variables: tuple[str, ...] = ()

    def seed_text(self) -> str:
        """Text für Langfuse: Variablen als {{name}}."""
        text = self.default
        for variable in self.variables:
            text = text.replace("{" + variable + "}", "{{" + variable + "}}")
        return text


@dataclass(frozen=True)
class ResolvedPrompt:
    key: str
    name: str
    text: str                      # Vorlage mit {{name}}-Platzhaltern
    variables: tuple[str, ...]
    source: str                    # "langfuse" oder "code"
    version: int | None            # Langfuse-Version, None beim Prompt aus dem Code

    def compile(self, **values) -> str:
        """Bekannte Variablen in einem Durchgang ersetzen, damit eingesetzte Werte nicht erneut ersetzt werden."""
        known = set(self.variables)
        return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in known else m.group(0), self.text)

    def info(self) -> dict:
        return {"name": self.name, "source": self.source, "version": self.version,
                "link": self.source == "langfuse"}


def _from_code(spec: PromptSpec) -> ResolvedPrompt:
    return ResolvedPrompt(spec.key, spec.name, spec.seed_text(), spec.variables, "code", None)


def _url(name: str) -> str:
    return f"{langfuse.host()}/api/public/v2/prompts/{quote(name, safe='')}"


def _auth() -> aiohttp.BasicAuth:
    public, secret = langfuse.auth()
    return aiohttp.BasicAuth(public, secret)


async def _fetch(session: aiohttp.ClientSession, spec: PromptSpec) -> ResolvedPrompt | None:
    async with session.get(_url(spec.name), params={"label": "production"}, auth=_auth()) as response:
        if response.status == 404:
            return None
        response.raise_for_status()
        data = await response.json(content_type=None)
    text = data.get("prompt")
    if data.get("type") != "text" or not isinstance(text, str) or not text.strip():
        log.warning("Prompt %s in Langfuse ist kein Text-Prompt, nutze Code-Fassung", spec.name)
        return None
    missing = [v for v in spec.variables if "{{" + v + "}}" not in text]
    if missing:
        log.warning("Prompt %s in Langfuse fehlen Variablen %s, nutze Code-Fassung", spec.name, missing)
        return None
    return ResolvedPrompt(spec.key, spec.name, text, spec.variables, "langfuse", int(data["version"]))


async def _seed(session: aiohttp.ClientSession, specs: list[PromptSpec]) -> None:
    created: list[str] = []
    for spec in specs:
        try:
            async with session.get(_url(spec.name), auth=_auth()) as response:
                if response.status != 404:
                    response.raise_for_status()
                    continue
            body = {"name": spec.name, "type": "text", "prompt": spec.seed_text(), "labels": ["production"],
                    "commitMessage": "Ausgangsfassung aus dem Code"}
            async with session.post(f"{langfuse.host()}/api/public/v2/prompts", json=body, auth=_auth()) as response:
                response.raise_for_status()
            created.append(spec.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("Langfuse-Prompt %s nicht angelegt (%s)", spec.name, type(exc).__name__)
    if created:
        log.info("Langfuse: Prompts angelegt: %s", ", ".join(created))


async def resolve_all(specs: list[PromptSpec]) -> dict[str, ResolvedPrompt]:
    """Alle Prompts für einen Lauf. Wirft nie. Legt beim ersten Aufruf je Prozess fehlende Prompts an."""
    global _seeded
    if not langfuse.enabled():
        return {spec.key: _from_code(spec) for spec in specs}
    resolved: dict[str, ResolvedPrompt] = {}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            if not _seeded:
                _seeded = True
                await _seed(session, specs)
            for spec in specs:
                try:
                    fetched = await _fetch(session, spec)
                except Exception as exc:  # noqa: BLE001 - Rückfall auf die Code-Fassung
                    log.warning("Langfuse-Prompt %s nicht ladbar (%s), nutze Code-Fassung", spec.name,
                                type(exc).__name__)
                    fetched = None
                resolved[spec.key] = fetched or _from_code(spec)
    except Exception as exc:  # noqa: BLE001
        log.warning("Langfuse-Prompts nicht ladbar (%s), nutze Code-Fassung", type(exc).__name__)
    return {spec.key: resolved.get(spec.key) or _from_code(spec) for spec in specs}
