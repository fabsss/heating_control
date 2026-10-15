from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, replace

import aiohttp

from .config import AnalysisConfig


class LlmError(Exception):
    pass


class ZdrUnavailable(LlmError):
    pass


class InvalidResponse(LlmError):
    partial = None   # LlmResult der bereits bezahlten Aufrufe


class CostLimitExceeded(LlmError):
    pass


class _Retry(Exception):
    pass


@dataclass
class LlmResult:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float


def _models_url(cfg: AnalysisConfig) -> str:
    return cfg.openrouter_url.replace("/chat/completions", "/models")


async def fetch_pricing(cfg: AnalysisConfig) -> tuple[float, float] | None:
    """Preis des Modells (USD je Million Token) aus der öffentlichen OpenRouter-Modellliste, sonst None."""
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(_models_url(cfg)) as response:
                if response.status != 200:
                    return None
                data = await response.json(content_type=None)
        for entry in data["data"]:
            if entry.get("id") == cfg.model:
                pricing = entry["pricing"]
                prompt, completion = float(pricing["prompt"]) * 1e6, float(pricing["completion"]) * 1e6
                return (prompt, completion) if prompt >= 0 and completion >= 0 else None
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
        return None
    return None


async def with_live_prices(cfg: AnalysisConfig) -> AnalysisConfig:
    """Preise aus Umgebungsvariablen gelten zuerst, danach OpenRouter, zuletzt die Datei."""
    if cfg.price_source == "env":
        return cfg
    prices = await fetch_pricing(cfg)
    if prices is None:
        return cfg
    return replace(cfg, price_input=prices[0], price_output=prices[1], price_source="openrouter")


def estimate_cost(messages: list[dict], cfg: AnalysisConfig) -> float:
    """Obere Schätzung: drei Zeichen je Eingabetoken, Ausgabe bis zum Limit."""
    characters = sum(len(message["content"]) for message in messages)
    return ((characters / 3) * cfg.price_input + cfg.max_output_tokens * cfg.price_output) / 1_000_000


def _check_cost(messages: list[dict], cfg: AnalysisConfig, spent: float) -> None:
    total = spent + estimate_cost(messages, cfg)
    if total > cfg.max_cost_usd:
        raise CostLimitExceeded(
            f"Geschätzte Kosten {total:.2f} USD übersteigen das Limit von {cfg.max_cost_usd:.2f} USD je Lauf")


def _result(body: str, cfg: AnalysisConfig) -> LlmResult:
    try:
        data = json.loads(body)
        text = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LlmError(f"unerwartete Antwort von OpenRouter: {body[:200]}") from exc
    usage = data.get("usage") or {}
    tokens_in, tokens_out = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
    if usage.get("cost") is not None:
        cost = float(usage["cost"])
    else:
        cost = (tokens_in * cfg.price_input + tokens_out * cfg.price_output) / 1_000_000
    return LlmResult(text or "", tokens_in, tokens_out, cost)


async def complete(cfg: AnalysisConfig, messages: list[dict], api_key: str, *,
                   sleep=asyncio.sleep) -> LlmResult:
    """Ein Aufruf mit Zero Data Retention. Netzfehler und 429/5xx werden wiederholt."""
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_output_tokens,
        "provider": cfg.provider,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=cfg.request_timeout_s)
    last: Exception | None = None
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(cfg.retries + 1):
            try:
                async with session.post(cfg.openrouter_url, json=payload, headers=headers) as response:
                    status, body = response.status, await response.text()
                if status == 404:
                    raise ZdrUnavailable(
                        f"Kein OpenRouter-Anbieter erfüllt Zero Data Retention für {cfg.model} "
                        f"(HTTP 404: {body[:200]})")
                if status in (429, 500, 502, 503, 504):
                    raise _Retry(f"HTTP {status}")
                if status >= 400:
                    raise LlmError(f"OpenRouter antwortet mit HTTP {status}: {body[:300]}")
                return _result(body, cfg)
            except (aiohttp.ClientError, asyncio.TimeoutError, _Retry) as exc:
                last = exc
                if attempt < cfg.retries:
                    await sleep(cfg.retry_backoff_s * 2 ** attempt)
    raise LlmError(f"Anfrage an OpenRouter fehlgeschlagen: {last}")


def parse_candidates_json(text: str, max_candidates: int) -> list[dict]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except ValueError as exc:
        raise InvalidResponse(f"kein gültiges JSON: {exc}") from exc
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("candidates"), list):
        items = data["candidates"]
    else:
        raise InvalidResponse('Schlüssel "candidates" (Liste) fehlt')
    if any(not isinstance(item, dict) for item in items):
        raise InvalidResponse("Die Einträge von candidates müssen Objekte sein")
    return items[:max_candidates]


async def request_candidates(cfg: AnalysisConfig, messages: list[dict], api_key: str, *,
                             sleep=asyncio.sleep) -> tuple[list[dict], LlmResult]:
    """Fragt Kandidaten an; bei ungültiger Antwort genau ein Wiederholungsversuch mit der Fehlermeldung."""
    total = LlmResult("", 0, 0, 0.0)
    current = list(messages)
    for attempt in range(2):
        _check_cost(current, cfg, total.cost_usd)
        result = await complete(cfg, current, api_key, sleep=sleep)
        total = LlmResult(result.text, total.tokens_in + result.tokens_in,
                          total.tokens_out + result.tokens_out, total.cost_usd + result.cost_usd)
        try:
            return parse_candidates_json(result.text, cfg.max_candidates), total
        except InvalidResponse as exc:
            if attempt == 1:
                exc.partial = total
                raise
            current = [*messages,
                       {"role": "assistant", "content": result.text},
                       {"role": "user", "content": f"Die Antwort war ungültig ({exc}). "
                                                   "Antworte ausschließlich mit gültigem JSON im vorgegebenen Format."}]
    raise InvalidResponse("unerreichbar")   # pragma: no cover
