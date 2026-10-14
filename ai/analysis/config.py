from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from collector.models import parse_utc

TOP_KEYS = {
    "model", "openrouter_url", "provider", "price_per_mtok", "max_output_tokens", "max_cost_usd",
    "temperature", "request_timeout_s", "retries", "retry_backoff_s", "max_candidates", "timezone",
    "min_interventions", "merge_interventions_s", "room_names", "exclusions", "context", "clusters",
    "backtest",
}
CONTEXT_KEYS = {"flat_state", "in_bed", "office_day", "outside_temperature"}


class AnalysisConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Exclusion:
    room: str
    start: datetime
    end: datetime | None


@dataclass(frozen=True)
class BacktestParams:
    match_window_min: int = 30
    contrary_window_min: int = 60
    setpoint_tolerance: float = 0.5
    min_faelle: int = 5
    min_wochen: int = 3
    min_abdeckung: float = 0.3
    max_fehlausloesungen: float = 0.4


@dataclass(frozen=True)
class ClusterParams:
    min_size: int = 3
    window_min: int = 90
    weekday_groups: dict[str, tuple[int, ...]] = field(
        default_factory=lambda: {"werktage": (0, 1, 2, 3, 4), "wochenende": (5, 6)})


@dataclass(frozen=True)
class AnalysisConfig:
    model: str
    openrouter_url: str
    provider: dict[str, Any]
    price_input: float
    price_output: float
    max_output_tokens: int
    max_cost_usd: float
    temperature: float
    request_timeout_s: float
    retries: int
    retry_backoff_s: float
    max_candidates: int
    timezone: str
    min_interventions: int
    merge_interventions_s: int
    room_names: dict[str, str]
    exclusions: tuple[Exclusion, ...]
    context: dict[str, str]
    clusters: ClusterParams
    backtest: BacktestParams


def _only(where: str, data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise AnalysisConfigError(f"{where}: unbekannte Schlüssel {sorted(unknown)}")


def _clusters(raw: dict[str, Any]) -> ClusterParams:
    _only("clusters", raw, {"min_size", "window_min", "weekday_groups"})
    defaults = ClusterParams()
    groups = defaults.weekday_groups
    if "weekday_groups" in raw:
        groups = {}
        for name, days in (raw["weekday_groups"] or {}).items():
            parsed = tuple(int(day) for day in days)
            if not parsed or any(day < 0 or day > 6 for day in parsed):
                raise AnalysisConfigError(f"clusters.weekday_groups.{name}: Wochentage müssen 0 bis 6 sein")
            groups[name] = parsed
    return ClusterParams(min_size=int(raw.get("min_size", defaults.min_size)),
                         window_min=int(raw.get("window_min", defaults.window_min)),
                         weekday_groups=groups)


def load_analysis_config(path: str | Path) -> AnalysisConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    _only("analysis.yaml", raw, TOP_KEYS)
    model = raw.get("model")
    if not model:
        raise AnalysisConfigError("model fehlt")
    provider = dict(raw.get("provider") or {})
    if provider.get("zdr") is not True:
        raise AnalysisConfigError("provider.zdr muss true sein: Zero Data Retention ist Pflicht")
    timezone = str(raw.get("timezone", "Europe/Berlin"))
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise AnalysisConfigError(f"timezone: unbekannte Zeitzone {timezone}") from exc
    price = raw.get("price_per_mtok") or {}
    try:
        price_input, price_output = float(price["input"]), float(price["output"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalysisConfigError("price_per_mtok braucht input und output (Zahlen)") from exc
    context = dict(raw.get("context") or {})
    missing = CONTEXT_KEYS - set(context)
    if missing:
        raise AnalysisConfigError(f"context: Einträge fehlen: {sorted(missing)}")
    _only("context", context, CONTEXT_KEYS)
    exclusions = []
    for item in raw.get("exclusions") or []:
        _only("exclusions", item, {"room", "from", "to"})
        if "room" not in item or "from" not in item:
            raise AnalysisConfigError("exclusions: room und from sind Pflicht")
        end = parse_utc(str(item["to"])) if item.get("to") else None
        exclusions.append(Exclusion(str(item["room"]), parse_utc(str(item["from"])), end))
    backtest_raw = dict(raw.get("backtest") or {})
    _only("backtest", backtest_raw, {f.name for f in fields(BacktestParams)})
    return AnalysisConfig(
        model=str(model),
        openrouter_url=str(raw.get("openrouter_url", "https://openrouter.ai/api/v1/chat/completions")),
        provider=provider,
        price_input=price_input,
        price_output=price_output,
        max_output_tokens=int(raw.get("max_output_tokens", 8000)),
        max_cost_usd=float(raw.get("max_cost_usd", 2.0)),
        temperature=float(raw.get("temperature", 0.2)),
        request_timeout_s=float(raw.get("request_timeout_s", 180)),
        retries=int(raw.get("retries", 3)),
        retry_backoff_s=float(raw.get("retry_backoff_s", 2.0)),
        max_candidates=int(raw.get("max_candidates", 20)),
        timezone=timezone,
        min_interventions=int(raw.get("min_interventions", 20)),
        merge_interventions_s=int(raw.get("merge_interventions_s", 60)),
        room_names={str(k): str(v) for k, v in (raw.get("room_names") or {}).items()},
        exclusions=tuple(exclusions),
        context={key: str(value) for key, value in context.items()},
        clusters=_clusters(dict(raw.get("clusters") or {})),
        backtest=BacktestParams(**backtest_raw),
    )
