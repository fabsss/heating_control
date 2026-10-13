from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

POLICY_KEYS = {"attrs", "change_attrs", "delta_attrs", "numeric_state_delta", "heartbeat_s", "debounce_s"}
EXCEPTION_KEYS = POLICY_KEYS | {"kind", "domain"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    ha_url: str
    label: str
    database: str
    flush_interval_s: float
    registry_sync_interval_s: float
    backup_path: str | None
    backup_time: str
    timezone: str
    domain_defaults: dict[str, dict[str, str]]
    device_class_kinds: dict[str, str]
    policies: dict[str, dict[str, Any]]
    device_class_policies: dict[str, dict[str, Any]]
    default_policy: dict[str, Any]
    exceptions: dict[str, dict[str, Any]]


def _check_keys(where: str, data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{where}: unbekannte Schlüssel {sorted(unknown)}")


def load_config(path: str | Path, env: dict[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    ha = raw.get("home_assistant") or {}
    url = env.get("HA_URL") or ha.get("url")
    if not url:
        raise ConfigError("home_assistant.url fehlt (oder Umgebungsvariable HA_URL setzen)")
    label = raw.get("label")
    if not label:
        raise ConfigError("label fehlt")
    policies = raw.get("policies") or {}
    for kind, policy in policies.items():
        _check_keys(f"policies.{kind}", policy, POLICY_KEYS)
    device_class_policies = raw.get("device_class_policies") or {}
    for device_class, policy in device_class_policies.items():
        _check_keys(f"device_class_policies.{device_class}", policy, POLICY_KEYS)
    default_policy = raw.get("default_policy") or {}
    _check_keys("default_policy", default_policy, POLICY_KEYS)
    exceptions = raw.get("exceptions") or {}
    for entity_id, exception in exceptions.items():
        _check_keys(f"exceptions.{entity_id}", exception, EXCEPTION_KEYS)
    backup = raw.get("backup") or {}
    return Config(
        ha_url=url,
        label=label,
        database=env.get("DB_PATH") or raw.get("database", "/data/home_ai.sqlite"),
        flush_interval_s=float(raw.get("flush_interval_s", 30)),
        registry_sync_interval_s=float(raw.get("registry_sync_interval_s", 600)),
        backup_path=backup.get("path"),
        backup_time=str(backup.get("time", "03:30")),
        timezone=str(raw.get("timezone", "UTC")),
        domain_defaults=raw.get("domain_defaults") or {},
        device_class_kinds=raw.get("device_class_kinds") or {},
        policies=policies,
        device_class_policies=device_class_policies,
        default_policy=default_policy,
        exceptions=exceptions,
    )
