from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


class ExecutionConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RoomConfig:
    area: str
    key: str
    heating_automation: str


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int = 1883
    user: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class ExecutionConfig:
    ha_url: str
    ha_token: str | None
    rooms: dict[str, RoomConfig]
    mqtt: MqttConfig | None = None
    timezone: str = "Europe/Berlin"
    blueprint_path: str = "homeai/ki_regel.yaml"
    dashboard_path: str = "ki-regeln"
    min_distance_min: int = 60
    blocked_states: tuple[str, ...] = ("Vacation",)
    setpoint_min: float = 15.0
    setpoint_max: float = 25.0
    eval_before_min: int = 60
    eval_after_min: int = 90
    target_tolerance: float = 0.5
    min_scored: int = 10
    max_fail_rate: float = 0.2
    llm_runs_per_day: int = 3
    arm_seconds: int = 60
    job_output_limit: int = 12000

    def require_token(self) -> str:
        if not self.ha_token:
            raise ExecutionConfigError("Umgebungsvariable HA_WRITE_TOKEN fehlt (Long-Lived Access Token mit Schreibrechten)")
        return self.ha_token

    def room_by_key(self, key: str) -> RoomConfig:
        for room in self.rooms.values():
            if room.key == key:
                return room
        raise ExecutionConfigError(f"Unbekannter Raum {key!r} (bekannt: {', '.join(r.key for r in self.rooms.values())})")


def load_execution_config(path: str | Path, env: dict[str, str] | None = None) -> ExecutionConfig:
    env = os.environ if env is None else env
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    url = env.get("HA_URL") or (raw.get("home_assistant") or {}).get("url")
    if not url:
        raise ExecutionConfigError("HA_URL fehlt (Umgebungsvariable oder home_assistant.url)")
    rooms: dict[str, RoomConfig] = {}
    for area, item in (raw.get("rooms") or {}).items():
        if not isinstance(item, dict) or "key" not in item or "heating_automation" not in item:
            raise ExecutionConfigError(f"rooms.{area}: key und heating_automation sind nötig")
        rooms[str(area)] = RoomConfig(str(area), str(item["key"]), str(item["heating_automation"]))
    if not rooms:
        raise ExecutionConfigError("rooms fehlt")
    keys = [room.key for room in rooms.values()]
    if len(set(keys)) != len(keys):
        raise ExecutionConfigError("rooms: Schlüssel doppelt vergeben")
    host = env.get("MQTT_HOST")
    mqtt = MqttConfig(host, int(env.get("MQTT_PORT") or 1883), env.get("MQTT_USER") or None,
                      env.get("MQTT_PASSWORD") or None) if host else None
    rule, evaluation, jobs = raw.get("rule") or {}, raw.get("evaluation") or {}, raw.get("jobs") or {}
    return ExecutionConfig(
        ha_url=url, ha_token=env.get("HA_WRITE_TOKEN") or None, rooms=rooms, mqtt=mqtt,
        timezone=str(raw.get("timezone", "Europe/Berlin")),
        blueprint_path=str(raw.get("blueprint_path", "homeai/ki_regel.yaml")),
        dashboard_path=str(raw.get("dashboard_path", "ki-regeln")),
        min_distance_min=int(rule.get("min_distance_min", 60)),
        blocked_states=tuple(rule.get("blocked_states", ["Vacation"])),
        setpoint_min=float(rule.get("setpoint_min", 15)), setpoint_max=float(rule.get("setpoint_max", 25)),
        eval_before_min=int(evaluation.get("before_min", 60)), eval_after_min=int(evaluation.get("after_min", 90)),
        target_tolerance=float(evaluation.get("target_tolerance", 0.5)),
        min_scored=int(evaluation.get("min_scored", 10)), max_fail_rate=float(evaluation.get("max_fail_rate", 0.2)),
        llm_runs_per_day=int(jobs.get("llm_runs_per_day", 3)), arm_seconds=int(jobs.get("arm_seconds", 60)),
        job_output_limit=int(jobs.get("output_limit", 12000)),
    )
