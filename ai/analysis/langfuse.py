"""Analyse-Läufe an Langfuse melden: ein Trace je Lauf mit Schritten, Modellaufrufen und Scores.

Langfuse v4 nimmt Traces nur noch über OpenTelemetry (OTLP/HTTP) an, die alte Ingestion-Schnittstelle lehnt
sie ab (sie antwortet trotzdem mit HTTP 207, die Ablehnung steht im Körper). Deshalb baut dieses Modul die
OTLP/JSON-Nachricht selbst, ohne OpenTelemetry-SDK. Nur Scores gehen weiter über ``/api/public/scores``.

Der Lauf ist ein kurzlebiger Auftrag (CLI oder Dashboard-Job). Gesendet wird deshalb einmal am Ende des
Laufs und abgewartet (``trace_scope``), sonst gehen die Daten beim Prozessende verloren. Ein Fehler beim
Senden stört den Lauf nie.

Ohne LANGFUSE_BASE_URL (oder LANGFUSE_HOST) und beide Schlüssel ist alles aus.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import time
import uuid
from typing import ClassVar

import aiohttp

log = logging.getLogger(__name__)
_TIMEOUT_S = 10
_MAX_TEXT = 100_000
_OTLP_PATH = "/api/public/otel/v1/traces"


def host() -> str:
    return (os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or "").strip().rstrip("/")


def auth() -> tuple[str, str]:
    return os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip(), os.environ.get("LANGFUSE_SECRET_KEY", "").strip()


def enabled() -> bool:
    public, secret = auth()
    return bool(host() and public and secret)


def release() -> str:
    return os.environ.get("APP_VERSION", "").strip()


def scrub(value):
    """Überlange Texte kürzen. Die Daten im Prompt sind bereits pseudonymisiert und auf Datenschutz geprüft."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str) and len(value) > _MAX_TEXT:
        return value[:_MAX_TEXT] + f" … [gekürzt, {len(value)} Zeichen]"
    return value


def _text(value) -> str:
    """Wert als Text für ein Attribut: Zeichenketten unverändert, alles andere als JSON."""
    return value if isinstance(value, str) else json.dumps(scrub(value), default=str, ensure_ascii=False)


def _attr(key: str, value) -> dict:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": _text(value)}}


def _nanos(ts: float) -> str:
    return str(int(ts * 1_000_000_000))


class Trace:
    """Ein Lauf mit Modellaufrufen, Zwischenschritten und Scores. Gesendet wird beim Beenden."""

    active = True

    def __init__(self, name: str, *, session_id: str | None = None, input=None, metadata: dict | None = None):
        self.id = uuid.uuid4().hex
        self.root_id = uuid.uuid4().hex[:16]
        self.name = name
        self.session_id = session_id
        self.input = input
        self.output = None
        self.error: str | None = None
        self.metadata = dict(metadata or {})
        self.started = time.time()
        self.last_end = self.started
        self.prompts: dict[str, dict] = {}   # Schlüssel -> Angaben zum benutzten Prompt (für die Generation)
        self._spans: list[dict] = []
        self._scores: list[dict] = []

    def use_prompt(self, key: str, info: dict) -> None:
        self.prompts[key] = info

    def _add(self, name: str, started: float, ended: float, attrs: dict, *, error: str | None) -> None:
        if error:
            attrs["langfuse.observation.level"] = "ERROR"
            attrs["langfuse.observation.status_message"] = error
        self._spans.append({
            "traceId": self.id, "spanId": uuid.uuid4().hex[:16], "parentSpanId": self.root_id, "name": name,
            "kind": 1, "startTimeUnixNano": _nanos(started), "endTimeUnixNano": _nanos(ended),
            "attributes": [_attr(k, v) for k, v in attrs.items() if v is not None],
            "status": {"code": 2, "message": error} if error else {"code": 1}})
        self.last_end = ended

    @staticmethod
    def _metadata_attrs(prefix: str, metadata: dict | None) -> dict:
        return {f"{prefix}.{k}": v for k, v in (metadata or {}).items() if v is not None}

    def generation(self, name: str, *, started: float, ended: float, model: str | None, input, output,
                   tokens_in: int = 0, tokens_out: int = 0, cost_usd: float | None = None,
                   error: str | None = None, metadata: dict | None = None, prompt: str | None = None) -> None:
        info = self.prompts.get(prompt or "", {})
        attrs = {
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": model or None,
            "langfuse.observation.usage_details": {"input": tokens_in, "output": tokens_out,
                                                    "total": tokens_in + tokens_out},
            "langfuse.observation.cost_details": {"total": float(cost_usd)} if cost_usd is not None else None,
            "langfuse.observation.input": input,
            "langfuse.observation.output": output,
            **self._metadata_attrs("langfuse.observation.metadata", {
                **(metadata or {}), **{"prompt_" + k: v for k, v in info.items() if k != "link"}}),
        }
        if info.get("link"):
            attrs["langfuse.observation.prompt.name"] = info["name"]
            attrs["langfuse.observation.prompt.version"] = info["version"]
        self._add(name, started, ended, attrs, error=error)

    def span(self, name: str, *, input=None, output=None, started: float | None = None,
             ended: float | None = None, error: str | None = None, metadata: dict | None = None) -> None:
        started = self.last_end if started is None else started
        ended = time.time() if ended is None else ended
        attrs = {"langfuse.observation.type": "span", "langfuse.observation.input": input,
                 "langfuse.observation.output": output,
                 **self._metadata_attrs("langfuse.observation.metadata", metadata)}
        self._add(name, started, ended, attrs, error=error)

    @contextlib.contextmanager
    def step(self, name: str, *, input=None, metadata: dict | None = None):
        """Zeitlich erfasster Schritt. Das Ergebnis setzt der Aufrufer in ``s["output"]``."""
        s: dict = {"output": None}
        t0 = time.time()
        try:
            yield s
        except BaseException as exc:
            self.span(name, input=input, output=s["output"], started=t0, error=type(exc).__name__,
                      metadata=metadata)
            raise
        self.span(name, input=input, output=s["output"], started=t0, metadata=metadata)

    def score(self, name: str, value: float, comment: str | None = None) -> None:
        self._scores.append({"traceId": self.id, "name": name, "value": float(value), "dataType": "NUMERIC",
                             **({"comment": comment} if comment else {})})

    def scores(self) -> list[dict]:
        return list(self._scores)

    def _trace_attrs(self) -> dict:
        """Angaben zum Trace stehen an jedem Span, damit Filter in Langfuse zuverlässig greifen."""
        return {
            "langfuse.trace.name": self.name,
            "langfuse.session.id": self.session_id,
            "langfuse.release": release() or None,
            **self._metadata_attrs("langfuse.trace.metadata", self.metadata),
            **({"langfuse.trace.metadata.error": self.error} if self.error else {}),
        }

    def otlp(self) -> dict:
        ended = time.time()
        root_attrs = {"langfuse.observation.type": "span", "langfuse.observation.input": self.input,
                      "langfuse.observation.output": self.output}
        if self.error:
            root_attrs["langfuse.observation.level"] = "ERROR"
            root_attrs["langfuse.observation.status_message"] = self.error
        root = {"traceId": self.id, "spanId": self.root_id, "name": self.name, "kind": 1,
                "startTimeUnixNano": _nanos(self.started), "endTimeUnixNano": _nanos(ended),
                "attributes": [_attr(k, v) for k, v in root_attrs.items() if v is not None],
                "status": {"code": 2, "message": self.error} if self.error else {"code": 1}}
        trace_attrs = [_attr(k, v) for k, v in self._trace_attrs().items() if v is not None]
        spans = []
        for span in (root, *self._spans):
            spans.append({**span, "attributes": span["attributes"] + trace_attrs})
        return {"resourceSpans": [{
            "resource": {"attributes": [_attr("service.name", "home-ai")]},
            "scopeSpans": [{"scope": {"name": "home-ai.analysis"}, "spans": spans}]}]}


class _NullTrace:
    """Tut nichts, wenn Langfuse nicht eingerichtet ist. Gleiche Schnittstelle, kein ``if`` beim Aufrufer."""

    active = False
    output = None
    error = None
    metadata: ClassVar[dict] = {}

    def use_prompt(self, key, info): ...

    def generation(self, name, **kw): ...

    def span(self, name, **kw): ...

    @contextlib.contextmanager
    def step(self, name, **kw):
        yield {"output": None}

    def score(self, name, value, comment=None): ...


NULL_TRACE = _NullTrace()
current_trace: contextvars.ContextVar = contextvars.ContextVar("langfuse_current_trace", default=NULL_TRACE)


async def send(trace: Trace, *, session: aiohttp.ClientSession | None = None) -> None:
    """Sendet Trace und Scores. Wirft nie."""
    public, secret = auth()
    basic = aiohttp.BasicAuth(public, secret)
    owns = session is None
    session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_TIMEOUT_S))
    try:
        content = json.dumps(trace.otlp(), default=str, ensure_ascii=False).encode()
        async with session.post(host() + _OTLP_PATH, data=content, auth=basic,
                                headers={"Content-Type": "application/json",
                                         "x-langfuse-ingestion-version": "4"}) as response:
            if response.status >= 300:
                log.warning("Langfuse: Trace abgelehnt (HTTP %s)", response.status)
        for score in trace.scores():
            async with session.post(host() + "/api/public/scores", json=score, auth=basic) as response:
                if response.status >= 300:
                    log.warning("Langfuse: Score %s abgelehnt (HTTP %s)", score["name"], response.status)
    except Exception as exc:  # noqa: BLE001 - Beobachtung darf nie einen Lauf stören
        log.warning("Langfuse: Meldung fehlgeschlagen (%s)", type(exc).__name__)
    finally:
        if owns:
            await session.close()


@contextlib.asynccontextmanager
async def trace_scope(name: str, *, session_id: str | None = None, input=None, metadata: dict | None = None):
    """Ein Lauf als ein Trace. Gesendet und abgewartet wird beim Verlassen, auch bei einem Fehler."""
    if not enabled():
        yield NULL_TRACE
        return
    trace = Trace(name, session_id=session_id, input=input, metadata=metadata)
    token = current_trace.set(trace)
    try:
        yield trace
    except BaseException as exc:
        trace.error = type(exc).__name__
        raise
    finally:
        current_trace.reset(token)
        with contextlib.suppress(Exception):
            await asyncio.shield(send(trace))
