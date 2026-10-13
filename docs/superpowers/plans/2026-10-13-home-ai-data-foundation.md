# KI-Datenbasis (Teilprojekt 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Docker-Container zeichnet Zustandsänderungen aller mit `KI_DB_Sync` gelabelten Home-Assistant-Entitäten dauerhaft in SQLite auf und spielt die letzte Heizsaison aus alten Proxmox-Backups nach.

**Architecture:** Ein asyncio-Collector hängt per WebSocket an Home Assistant, ermittelt die Entitäten über das Label, wendet Speicherregeln (Schwellen, Entprellung, Attribut-Whitelist) an und schreibt gepuffert alle 30 s in `home_ai.sqlite`. Ein Backfill-Werkzeug liest eine alte `home-assistant_v2.db` nur lesend und nutzt dieselbe Umwandlung. Eine SQL-View `manual_actions` klassifiziert Thermostat-Eingriffe nach Verursacher.

**Tech Stack:** Python 3.12, asyncio, aiohttp (WebSocket-Client), PyYAML, sqlite3 (stdlib, WAL), pytest + pytest-asyncio, Docker.

**Spec:** `docs/superpowers/specs/2026-10-13-home-ai-data-foundation-design.md`

## Global Constraints

Diese Werte stammen aus der Spezifikation und gelten für jede Aufgabe:

- Verzeichnis: `ai/` im Repository; Datenbankdatei `home_ai.sqlite`; View `manual_actions`; Label `KI_DB_Sync`.
- Alle Zeiten UTC, ISO-8601 mit festen Mikrosekunden (`2026-01-01T10:00:00.000000+00:00`), damit Textvergleich der Zeit entspricht.
- SQLite im WAL-Modus; Schreiben gepuffert alle **30 Sekunden** und beim ordentlichen Beenden (SIGTERM); Intervall in `collector.yaml` konfigurierbar.
- Abgleich der Entity Registry beim Start und danach alle **600 Sekunden** (10 Minuten).
- **Einzige Quelle für die Mitgliedschaft ist das Label.** `collector.yaml` enthält nur Regeln (Standardwerte, Whitelist je `kind`, Ausnahmen je `entity_id`), keine Entitätenliste. Eine Ausnahme für eine Entität ohne Label hat keine Wirkung.
- `history_map.yaml` wird nur vom Backfill gelesen und beeinflusst die Live-Aufzeichnung nie.
- Speicherregeln: Thermostate speichern bei Änderung von `state`, `temperature`, `preset_mode`, `window_open` oder wenn `current_temperature` um mindestens 0,5 °C vom zuletzt *gespeicherten* Wert abweicht; Whitelist `temperature, preset_mode, hvac_action, current_temperature, current_humidity, window_open`. Temperatur-Sensoren 0,5 °C, Luftfeuchte-Sensoren 5 %, Heartbeat 1800 s. Schalter, Personen, Kontakt- und Präsenzsensoren, Zeitpläne: jede Änderung. Handy-Raumsensor: Raumwechsel erst nach 60 s Verweildauer. `unavailable`/`unknown` werden gespeichert.
- Verursacher-Klassen der View: `context_parent_id` gesetzt → `automation`; sonst `context_user_id` gesetzt → `manual`; sonst `unknown`. Nur `manual` ist Lernsignal.
- Nichts wird automatisch gelöscht: Label entfernt → `active = 0`, `tracked_until` gesetzt, Daten bleiben. Löschen nur über `purge-entity` mit Bestätigung.
- Fehlt das Label oder trägt es keine Entität, beendet sich der Collector mit klarer Meldung (kein stilles Nichtstun). Ungültiges Token beendet den Start.
- Token nur über Umgebungsvariable `HA_TOKEN`; Backups und Tokens werden nie committet.
- Rohdaten bleiben lokal; dieses Teilprojekt enthält keinen LLM-Aufruf.

## Review Focus

Fünf Eingaben beziehungsweise Fehlerfälle, die die Spezifikation voraussetzt, aber nicht ausdrücklich nennt. Jede Zeile hat einen Test in der genannten Aufgabe.

1. **`state_changed` ohne `new_state`** (Entität wurde gelöscht) darf den Collector nicht abstürzen lassen, die Änderung wird ignoriert. → Aufgabe 7, `test_change_from_event_ignores_removed_entity`.
2. **Attribute mit nicht serialisierbaren Werten** (Datum, verschachtelte Objekte) und `unavailable`-Zustände ohne Attribute werden ohne Fehler gespeichert. → Aufgabe 1 (`test_attrs_json_...`) und Aufgabe 3 (`test_insert_changes_stores_unavailable_without_attrs`).
3. **Schreibfehler beim Leeren des Puffers** darf keine Änderungen verlieren; der nächste Versuch schreibt sie. → Aufgabe 6, `test_flush_keeps_buffer_when_write_fails`.
4. **Zeitstempel in verschiedenen Schreibweisen** (`Z`, Offset, ohne Zeitzone) landen einheitlich als UTC. → Aufgabe 1, `test_parse_utc_accepts_z_offsets_and_naive`.
5. **Backfill-Quelle mit leeren Zeilen** (`state` ist NULL, Entität ohne Zeilen im Zeitraum, doppeltes Einspielen) bricht nicht ab und ist idempotent. → Aufgabe 8, `test_backfill_skips_null_states_and_is_idempotent`.

---

## Dateistruktur

```
ai/
  requirements.txt
  requirements-dev.txt
  pytest.ini
  .gitignore
  Dockerfile
  docker-compose.yml
  .env.example
  README.md
  config/
    collector.yaml            # Regeln (keine Entitätenliste)
    history_map.yaml          # nur Backfill (entsteht in Aufgabe 12)
  sql/
    schema.sql                # Tabellen, Indizes, View manual_actions
  collector/
    __init__.py
    __main__.py               # python -m collector ...
    models.py                 # EntityInfo, EntityRule, Change, Zeit-/Hash-Helfer
    config.py                 # Config, load_config
    rules.py                  # resolve_rule(): kind/domain/Speicherregel einer Entität
    filtering.py              # ChangeFilter: Speicherregeln + Entprellung
    db.py                     # Database: Schema, Upserts, Einfügen, Backup, Purge
    hass_ws.py                # HassClient: WebSocket-Anbindung
    registry.py               # fetch_labeled_entities, fetch_users
    pipeline.py               # Pipeline: Filter je Entität + Schreibpuffer
    live.py                   # Collector: Sitzung, Lückenabgleich, Perioden, Backup
    ids.py                    # ULID-/UUID-Umwandlung für HA-Datenbank
    backfill.py               # backfill_ha_db, history_map
    verify.py                 # Abgleich mit thermostat_actor_logged
    healthcheck.py            # Docker-Healthcheck
    cli.py                    # run | backfill | verify | purge-entity
  tests/
    __init__.py
    conftest.py               # Fixtures cfg, db, fake_hass
    fake_hass.py              # FakeHass (WebSocket-Attrappe)
    ha_fixture.py             # Baut eine kleine home-assistant_v2.db
    test_*.py
```

Befehle im Plan gelten für Git Bash unter Windows (virtuelle Umgebung unter `.venv/Scripts/`). Unter Linux heißt der Pfad `.venv/bin/python`. Alle Pfade sind relativ zum Repository-Wurzelverzeichnis, `cd ai` steht jeweils im Befehl.

---

### Task 1: Gerüst, Modelle, Konfiguration, Regelauflösung

**Files:**
- Create: `ai/requirements.txt`, `ai/requirements-dev.txt`, `ai/pytest.ini`, `ai/.gitignore`
- Create: `ai/collector/__init__.py`, `ai/collector/models.py`, `ai/collector/config.py`, `ai/collector/rules.py`
- Create: `ai/config/collector.yaml`
- Create: `ai/tests/__init__.py`, `ai/tests/conftest.py`, `ai/tests/test_models.py`, `ai/tests/test_config.py`, `ai/tests/test_rules.py`

**Interfaces:**
- Produces:
  - `collector.models`: `utcnow() -> datetime`, `to_utc_iso(dt) -> str`, `parse_utc(text) -> datetime`, `attrs_json(attrs) -> str`, `attrs_hash(attrs) -> str`, dataclasses `EntityInfo(entity_id, device_class=None, area=None)` mit Property `ha_domain`, `EntityRule(entity_id, kind, domain, attrs, change_attrs, delta_attrs, numeric_state_delta, heartbeat_s, debounce_s)`, `Change(ts, entity_id, state, attrs, context_id, context_user_id, context_parent_id)`.
  - `collector.config`: `Config`, `ConfigError`, `POLICY_KEYS`, `load_config(path, env=None) -> Config`.
  - `collector.rules`: `resolve_rule(info: EntityInfo, cfg: Config, override: dict | None = None) -> EntityRule`.
  - Fixture `cfg` (lädt `ai/config/collector.yaml`).

- [ ] **Step 1: Gerüstdateien anlegen**

`ai/requirements.txt`:

```
aiohttp>=3.9,<4
PyYAML>=6,<7
tzdata
```

`ai/requirements-dev.txt`:

```
-r requirements.txt
pytest>=8
pytest-asyncio>=0.23
```

`ai/pytest.ini`:

```
[pytest]
pythonpath = .
testpaths = tests
asyncio_mode = auto
```

`ai/.gitignore`:

```
.venv/
data/
backup/
.env
__pycache__/
.pytest_cache/
```

Leere Dateien `ai/collector/__init__.py` und `ai/tests/__init__.py` anlegen.

Run:

```bash
cd ai && python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
```

Expected: Installation endet ohne Fehler.

- [ ] **Step 2: Regelkonfiguration anlegen**

`ai/config/collector.yaml`:

```yaml
# Regeln des Collectors. Diese Datei enthält KEINE Entitätenliste:
# Welche Entitäten aufgezeichnet werden, bestimmt allein das Label in Home Assistant.
home_assistant:
  url: "ws://homeassistant.local:8123/api/websocket"   # per Umgebungsvariable HA_URL überschreibbar
label: "KI_DB_Sync"
database: "/data/home_ai.sqlite"                        # per Umgebungsvariable DB_PATH überschreibbar
flush_interval_s: 30
registry_sync_interval_s: 600
timezone: "Europe/Berlin"
backup:
  path: "/backup/home_ai.sqlite"
  time: "03:30"                                         # lokale Zeit

# Anwendungsdomäne und Art je Home-Assistant-Domäne
domain_defaults:
  climate:       {kind: climate,  domain: heating}
  schedule:      {kind: schedule, domain: heating}
  light:         {kind: light,    domain: lighting}
  scene:         {kind: scene,    domain: lighting}
  person:        {kind: person,   domain: shared}
  weather:       {kind: weather,  domain: shared}
  input_select:  {kind: flat,     domain: shared}
  input_boolean: {kind: flat,     domain: shared}
  input_button:  {kind: button,   domain: shared}
  binary_sensor: {kind: flat,     domain: shared}
  sensor:        {kind: env,      domain: shared}

# Art je device_class (nur sensor und binary_sensor)
device_class_kinds:
  occupancy: presence
  presence: presence
  motion: presence
  window: contact
  door: contact
  opening: contact
  garage_door: contact

# Standard: jede Änderung des Zustands wird gespeichert
default_policy: {}

# Speicherregeln je kind
policies:
  climate:
    attrs: [temperature, preset_mode, hvac_action, current_temperature, current_humidity, window_open]
    change_attrs: [temperature, preset_mode, window_open]
    delta_attrs: {current_temperature: 0.5}
  weather:
    attrs: [temperature, humidity, wind_speed]
    delta_attrs: {temperature: 0.5, humidity: 5, wind_speed: 5}
    heartbeat_s: 1800

# Speicherregeln je device_class (überschreiben die je kind)
device_class_policies:
  temperature: {numeric_state_delta: 0.5, heartbeat_s: 1800}
  humidity: {numeric_state_delta: 5, heartbeat_s: 1800}

# Ausnahmen je Entität; wirken nur, wenn die Entität das Label trägt
exceptions:
  input_boolean.heating_on_off: {domain: heating}
  input_boolean.heating_automation: {domain: heating}
  sensor.fabians_s22_presence_tracker_area_last_seen: {kind: presence, debounce_s: 60}
  binary_sensor.windows_livingroom_and_door: {kind: contact}
  binary_sensor.windows_bedroom: {kind: contact}
```

- [ ] **Step 3: Tests für die Modelle schreiben**

`ai/tests/conftest.py`:

```python
from pathlib import Path

import pytest

from collector.config import load_config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return load_config(
        ROOT / "config" / "collector.yaml",
        env={"HA_URL": "ws://ha.test/api/websocket", "DB_PATH": ":memory:"},
    )
```

`ai/tests/test_models.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from collector.models import attrs_hash, attrs_json, parse_utc, to_utc_iso


def test_to_utc_iso_has_fixed_microseconds_and_sorts():
    a = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    b = a + timedelta(microseconds=5)
    assert to_utc_iso(a) == "2026-01-01T10:00:00.000000+00:00"
    assert to_utc_iso(a) < to_utc_iso(b)


def test_to_utc_iso_converts_offset_to_utc():
    local = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone(timedelta(hours=1)))
    assert to_utc_iso(local) == "2026-01-01T10:00:00.000000+00:00"


def test_to_utc_iso_rejects_naive_datetime():
    with pytest.raises(ValueError):
        to_utc_iso(datetime(2026, 1, 1))


@pytest.mark.parametrize(
    "text",
    ["2026-01-01T10:00:00Z", "2026-01-01T11:00:00+01:00", "2026-01-01T10:00:00"],
)
def test_parse_utc_accepts_z_offsets_and_naive(text):
    assert parse_utc(text) == datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_attrs_json_is_stable_and_tolerates_odd_values():
    first = {"b": 1, "a": datetime(2026, 1, 1, tzinfo=timezone.utc), "n": {"x": [1, 2]}}
    second = {"n": {"x": [1, 2]}, "a": datetime(2026, 1, 1, tzinfo=timezone.utc), "b": 1}
    assert attrs_json(first) == attrs_json(second)
    assert attrs_hash(first) == attrs_hash(second)
    assert attrs_json({}) == "{}"
    assert attrs_hash({"a": 1}) != attrs_hash({"a": 2})
```

- [ ] **Step 4: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_models.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.models'`.

- [ ] **Step 5: Modelle implementieren**

`ai/collector/models.py`:

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(dt: datetime) -> str:
    """UTC-ISO-8601 mit festen Mikrosekunden; Textvergleich entspricht dem Zeitvergleich."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def attrs_json(attrs: dict[str, Any]) -> str:
    return json.dumps(attrs, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def attrs_hash(attrs: dict[str, Any]) -> str:
    return hashlib.sha1(attrs_json(attrs).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class EntityInfo:
    entity_id: str
    device_class: str | None = None
    area: str | None = None

    @property
    def ha_domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


@dataclass(frozen=True)
class EntityRule:
    entity_id: str
    kind: str
    domain: str
    attrs: tuple[str, ...] = ()
    change_attrs: tuple[str, ...] = ()
    delta_attrs: dict[str, float] = field(default_factory=dict)
    numeric_state_delta: float | None = None
    heartbeat_s: int | None = None
    debounce_s: int | None = None


@dataclass(frozen=True)
class Change:
    ts: datetime
    entity_id: str
    state: str | None
    attrs: dict[str, Any] = field(default_factory=dict)
    context_id: str | None = None
    context_user_id: str | None = None
    context_parent_id: str | None = None
```

- [ ] **Step 6: Modelltests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_models.py -v`
Expected: PASS (7 Tests).

- [ ] **Step 7: Tests für Konfiguration und Regelauflösung schreiben**

`ai/tests/test_config.py`:

```python
import pytest

from collector.config import ConfigError, load_config


def write(tmp_path, text):
    path = tmp_path / "c.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_config_loads(cfg):
    assert cfg.label == "KI_DB_Sync"
    assert cfg.flush_interval_s == 30
    assert cfg.registry_sync_interval_s == 600
    assert cfg.ha_url == "ws://ha.test/api/websocket"
    assert cfg.database == ":memory:"
    assert cfg.exceptions["sensor.fabians_s22_presence_tracker_area_last_seen"]["debounce_s"] == 60


def test_missing_url_raises(tmp_path):
    with pytest.raises(ConfigError, match="url"):
        load_config(write(tmp_path, "label: X\n"), env={})


def test_missing_label_raises(tmp_path):
    with pytest.raises(ConfigError, match="label"):
        load_config(write(tmp_path, "home_assistant: {url: 'ws://x'}\n"), env={})


def test_unknown_policy_key_raises(tmp_path):
    text = "home_assistant: {url: 'ws://x'}\nlabel: X\npolicies:\n  climate: {bogus: 1}\n"
    with pytest.raises(ConfigError, match="bogus"):
        load_config(write(tmp_path, text), env={})


def test_unknown_exception_key_raises(tmp_path):
    text = "home_assistant: {url: 'ws://x'}\nlabel: X\nexceptions:\n  sensor.a: {nope: 1}\n"
    with pytest.raises(ConfigError, match="nope"):
        load_config(write(tmp_path, text), env={})
```

`ai/tests/test_rules.py`:

```python
from dataclasses import replace

import pytest

from collector.config import ConfigError
from collector.models import EntityInfo
from collector.rules import resolve_rule


def test_better_thermostat_is_heating_climate(cfg):
    rule = resolve_rule(EntityInfo("climate.better_thermostat_office", None, "buero"), cfg)
    assert (rule.kind, rule.domain) == ("climate", "heating")
    assert rule.attrs == (
        "temperature", "preset_mode", "hvac_action",
        "current_temperature", "current_humidity", "window_open",
    )
    assert rule.change_attrs == ("temperature", "preset_mode", "window_open")
    assert rule.delta_attrs == {"current_temperature": 0.5}


def test_temperature_sensor_uses_device_class_policy(cfg):
    rule = resolve_rule(EntityInfo("sensor.environment_sensor_outside_temperature", "temperature"), cfg)
    assert (rule.kind, rule.domain) == ("env", "shared")
    assert rule.numeric_state_delta == 0.5
    assert rule.heartbeat_s == 1800


def test_humidity_sensor_threshold_is_five(cfg):
    rule = resolve_rule(EntityInfo("sensor.environment_sensor_outside_humidity", "humidity"), cfg)
    assert rule.numeric_state_delta == 5.0


def test_presence_binary_sensor_kind_comes_from_device_class(cfg):
    rule = resolve_rule(EntityInfo("binary_sensor.presence_office_presence", "occupancy", "buero"), cfg)
    assert (rule.kind, rule.domain) == ("presence", "shared")
    assert rule.numeric_state_delta is None


def test_exception_overrides_domain_of_heating_switch(cfg):
    rule = resolve_rule(EntityInfo("input_boolean.heating_on_off"), cfg)
    assert (rule.kind, rule.domain) == ("flat", "heating")


def test_area_sensor_gets_debounce_from_exception(cfg):
    rule = resolve_rule(EntityInfo("sensor.fabians_s22_presence_tracker_area_last_seen"), cfg)
    assert rule.kind == "presence"
    assert rule.debounce_s == 60


def test_weather_has_attribute_deltas(cfg):
    rule = resolve_rule(EntityInfo("weather.home"), cfg)
    assert rule.kind == "weather"
    assert rule.delta_attrs == {"temperature": 0.5, "humidity": 5.0, "wind_speed": 5.0}


def test_unknown_domain_falls_back_to_shared_flat(cfg):
    rule = resolve_rule(EntityInfo("foo.bar"), cfg)
    assert (rule.kind, rule.domain) == ("flat", "shared")


def test_override_is_applied_below_config_exceptions(cfg):
    rule = resolve_rule(EntityInfo("climate.trv_office"), cfg, override={"kind": "climate", "domain": "heating"})
    assert (rule.kind, rule.domain) == ("climate", "heating")
    assert rule.attrs[0] == "temperature"


def test_change_attrs_must_be_whitelisted(cfg):
    bad = replace(cfg, exceptions={"climate.x": {"attrs": ["temperature"], "change_attrs": ["preset_mode"]}})
    with pytest.raises(ConfigError, match="preset_mode"):
        resolve_rule(EntityInfo("climate.x"), bad)
```

- [ ] **Step 8: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_config.py tests/test_rules.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.config'`.

- [ ] **Step 9: Konfiguration und Regelauflösung implementieren**

`ai/collector/config.py`:

```python
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
```

`ai/collector/rules.py`:

```python
from __future__ import annotations

from typing import Any

from .config import POLICY_KEYS, Config, ConfigError
from .models import EntityInfo, EntityRule

FALLBACK = {"kind": "flat", "domain": "shared"}
CLASS_KIND_DOMAINS = {"sensor", "binary_sensor"}


def resolve_rule(info: EntityInfo, cfg: Config, override: dict[str, Any] | None = None) -> EntityRule:
    """Leitet Art, Anwendungsdomäne und Speicherregel einer gelabelten Entität ab.

    Reihenfolge (spätere gewinnen): default_policy, policies[kind],
    device_class_policies[device_class], override, exceptions[entity_id].
    """
    base = cfg.domain_defaults.get(info.ha_domain, FALLBACK)
    kind, domain = base["kind"], base["domain"]
    if info.ha_domain in CLASS_KIND_DOMAINS and info.device_class in cfg.device_class_kinds:
        kind = cfg.device_class_kinds[info.device_class]
    exc = {**(override or {}), **cfg.exceptions.get(info.entity_id, {})}
    kind = exc.get("kind", kind)
    domain = exc.get("domain", domain)

    policy: dict[str, Any] = dict(cfg.default_policy)
    policy.update(cfg.policies.get(kind, {}))
    if info.device_class:
        policy.update(cfg.device_class_policies.get(info.device_class, {}))
    policy.update({key: value for key, value in exc.items() if key in POLICY_KEYS})

    attrs = tuple(policy.get("attrs", ()))
    change_attrs = tuple(policy.get("change_attrs", ()))
    delta_attrs = {name: float(value) for name, value in dict(policy.get("delta_attrs", {})).items()}
    for name in (*change_attrs, *delta_attrs):
        if name not in attrs:
            raise ConfigError(f"{info.entity_id}: Attribut '{name}' steht nicht in attrs")
    delta = policy.get("numeric_state_delta")
    return EntityRule(
        entity_id=info.entity_id,
        kind=kind,
        domain=domain,
        attrs=attrs,
        change_attrs=change_attrs,
        delta_attrs=delta_attrs,
        numeric_state_delta=None if delta is None else float(delta),
        heartbeat_s=policy.get("heartbeat_s"),
        debounce_s=policy.get("debounce_s"),
    )
```

- [ ] **Step 10: Alle Tests dieser Aufgabe laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_models.py tests/test_config.py tests/test_rules.py -v`
Expected: PASS (alle Tests).

- [ ] **Step 11: Commit**

```bash
git add ai/requirements.txt ai/requirements-dev.txt ai/pytest.ini ai/.gitignore ai/config/collector.yaml ai/collector ai/tests
git commit -m "feat(ai): Projektgerüst, Modelle, Konfiguration und Regelauflösung"
```

---

### Task 2: ChangeFilter (Speicherregeln und Entprellung)

**Files:**
- Create: `ai/collector/filtering.py`
- Test: `ai/tests/test_filtering.py`

**Interfaces:**
- Consumes: `EntityRule`, `Change` aus Aufgabe 1.
- Produces: `class ChangeFilter(rule: EntityRule, last: Change | None = None)` mit `feed(change) -> list[Change]`, `tick(now: datetime) -> list[Change]`, `flush_pending() -> list[Change]`. Zurückgegebene `Change`-Objekte tragen nur Attribute der Whitelist. `last` ist die zuletzt *gespeicherte* Änderung.

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_filtering.py`:

```python
from datetime import datetime, timedelta, timezone

from collector.filtering import ChangeFilter
from collector.models import Change, EntityRule

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def climate_rule() -> EntityRule:
    return EntityRule(
        entity_id="climate.bt",
        kind="climate",
        domain="heating",
        attrs=("temperature", "preset_mode", "hvac_action", "current_temperature", "window_open"),
        change_attrs=("temperature", "preset_mode", "window_open"),
        delta_attrs={"current_temperature": 0.5},
    )


def climate(seconds, state="heat", **attrs) -> Change:
    base = {"temperature": 17, "preset_mode": "none", "hvac_action": "idle",
            "current_temperature": 20.0, "window_open": False, "heating_power": 0.01}
    base.update(attrs)
    return Change(at(seconds), "climate.bt", state, base)


def test_first_change_is_always_stored_and_trimmed_to_whitelist():
    f = ChangeFilter(climate_rule())
    out = f.feed(climate(0))
    assert len(out) == 1
    assert "heating_power" not in out[0].attrs
    assert out[0].attrs["temperature"] == 17


def test_unchanged_repeat_is_dropped():
    f = ChangeFilter(climate_rule())
    f.feed(climate(0))
    assert f.feed(climate(5)) == []


def test_setpoint_preset_mode_and_window_changes_are_stored():
    f = ChangeFilter(climate_rule())
    f.feed(climate(0))
    assert len(f.feed(climate(10, temperature=21))) == 1
    assert len(f.feed(climate(20, temperature=21, preset_mode="comfort"))) == 1
    assert len(f.feed(climate(30, temperature=21, preset_mode="comfort", window_open=True))) == 1
    assert len(f.feed(climate(40, state="off", temperature=21, preset_mode="comfort", window_open=True))) == 1


def test_hvac_action_alone_does_not_trigger_storing():
    f = ChangeFilter(climate_rule())
    f.feed(climate(0))
    assert f.feed(climate(10, hvac_action="heating")) == []


def test_current_temperature_drift_accumulates_against_last_stored_value():
    f = ChangeFilter(climate_rule())
    f.feed(climate(0, current_temperature=20.0))
    assert f.feed(climate(10, current_temperature=20.3)) == []
    assert len(f.feed(climate(20, current_temperature=20.6))) == 1
    assert f.feed(climate(30, current_temperature=20.9)) == []


def numeric_rule(delta=0.5, heartbeat=None) -> EntityRule:
    return EntityRule(entity_id="sensor.t", kind="env", domain="shared",
                      numeric_state_delta=delta, heartbeat_s=heartbeat)


def num(seconds, state) -> Change:
    return Change(at(seconds), "sensor.t", state)


def test_numeric_sensor_threshold_and_boundary():
    f = ChangeFilter(numeric_rule(0.5))
    assert len(f.feed(num(0, "19.1"))) == 1
    assert f.feed(num(1, "19.4")) == []
    assert len(f.feed(num(2, "19.6"))) == 1   # genau 0,5 Abstand zählt trotz Gleitkomma-Fehler


def test_numeric_sensor_stores_unavailable_transitions():
    f = ChangeFilter(numeric_rule(0.5))
    f.feed(num(0, "19.0"))
    assert len(f.feed(num(1, "unavailable"))) == 1
    assert f.feed(num(2, "unavailable")) == []
    assert len(f.feed(num(3, "19.1"))) == 1


def test_heartbeat_stores_unchanged_value_after_interval():
    f = ChangeFilter(numeric_rule(0.5, heartbeat=1800))
    f.feed(num(0, "19.0"))
    assert f.feed(num(1799, "19.0")) == []
    assert len(f.feed(num(1800, "19.0"))) == 1


def test_every_change_policy_stores_each_state_change_only():
    rule = EntityRule(entity_id="input_boolean.x", kind="flat", domain="shared")
    f = ChangeFilter(rule)
    assert len(f.feed(Change(at(0), "input_boolean.x", "off"))) == 1
    assert f.feed(Change(at(1), "input_boolean.x", "off")) == []
    assert len(f.feed(Change(at(2), "input_boolean.x", "on"))) == 1


def test_filter_can_be_seeded_with_last_stored_change():
    f = ChangeFilter(numeric_rule(0.5), last=num(0, "19.0"))
    assert f.feed(num(5, "19.2")) == []


def debounce_rule() -> EntityRule:
    return EntityRule(entity_id="sensor.room", kind="presence", domain="shared", debounce_s=60)


def room(seconds, state) -> Change:
    return Change(at(seconds), "sensor.room", state)


def test_debounce_suppresses_flapping_room_changes():
    f = ChangeFilter(debounce_rule())
    assert f.feed(room(0, "Büro")) == []        # noch nicht bestätigt
    assert f.flush_pending()[0].state == "Büro"  # erster Wert wird bestätigt (Ende des Stroms)
    assert f.feed(room(100, "Flur")) == []
    assert f.feed(room(110, "Büro")) == []       # zurück innerhalb von 60 s -> Flur verworfen
    assert f.tick(at(300)) == []                 # Büro ist unverändert, nichts zu speichern


def test_debounce_confirms_room_held_long_enough_on_next_event():
    f = ChangeFilter(debounce_rule())
    f.feed(room(0, "Büro"))
    f.flush_pending()
    f.feed(room(100, "Flur"))
    out = f.feed(room(170, "Küche"))             # Flur hielt 70 s -> bestätigt
    assert [c.state for c in out] == ["Flur"]


def test_debounce_confirms_via_tick():
    f = ChangeFilter(debounce_rule())
    f.feed(room(0, "Büro"))
    assert f.tick(at(59)) == []
    assert [c.state for c in f.tick(at(61))] == ["Büro"]
    assert f.tick(at(200)) == []
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_filtering.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.filtering'`.

- [ ] **Step 3: Implementieren**

`ai/collector/filtering.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import Change, EntityRule

EPSILON = 1e-9


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ChangeFilter:
    """Entscheidet pro Entität, welche Zustandsänderungen gespeichert werden.

    Verglichen wird immer mit der zuletzt *gespeicherten* Änderung, damit langsame
    Drifts nicht verloren gehen. Mit `debounce_s` wird ein Wert erst gespeichert,
    wenn er mindestens so lange gehalten wurde.
    """

    def __init__(self, rule: EntityRule, last: Change | None = None):
        self.rule = rule
        self.last: Change | None = self._trim(last) if last is not None else None
        self.pending: Change | None = None

    def _trim(self, change: Change) -> Change:
        attrs = {name: change.attrs[name] for name in self.rule.attrs if name in change.attrs}
        return Change(change.ts, change.entity_id, change.state, attrs,
                      change.context_id, change.context_user_id, change.context_parent_id)

    def _differs(self, new: Change) -> bool:
        last = self.last
        if last is None:
            return True
        rule = self.rule
        if rule.numeric_state_delta is not None:
            old_value, new_value = _num(last.state), _num(new.state)
            if old_value is not None and new_value is not None:
                if abs(new_value - old_value) >= rule.numeric_state_delta - EPSILON:
                    return True
            elif last.state != new.state:
                return True
        elif last.state != new.state:
            return True
        for name in rule.change_attrs:
            if last.attrs.get(name) != new.attrs.get(name):
                return True
        for name, delta in rule.delta_attrs.items():
            old_value, new_value = _num(last.attrs.get(name)), _num(new.attrs.get(name))
            if old_value is not None and new_value is not None:
                if abs(new_value - old_value) >= delta - EPSILON:
                    return True
            elif last.attrs.get(name) != new.attrs.get(name):
                return True
        if rule.heartbeat_s is not None and (new.ts - last.ts).total_seconds() >= rule.heartbeat_s:
            return True
        return False

    def feed(self, change: Change) -> list[Change]:
        if self.rule.debounce_s:
            confirmed = self._confirm(change.ts)
            self.pending = self._trim(change) if self._differs(change) else None
            return confirmed
        if self._differs(change):
            self.last = self._trim(change)
            return [self.last]
        return []

    def tick(self, now: datetime) -> list[Change]:
        return self._confirm(now) if self.rule.debounce_s else []

    def flush_pending(self) -> list[Change]:
        """Bestätigt einen wartenden Wert sofort (Ende eines Datenstroms)."""
        pending, self.pending = self.pending, None
        if pending is not None and self._differs(pending):
            self.last = pending
            return [pending]
        return []

    def _confirm(self, now: datetime) -> list[Change]:
        pending = self.pending
        if pending is None or (now - pending.ts).total_seconds() < self.rule.debounce_s:
            return []
        return self.flush_pending()
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_filtering.py -v`
Expected: PASS (13 Tests).

- [ ] **Step 5: Commit**

```bash
git add ai/collector/filtering.py ai/tests/test_filtering.py
git commit -m "feat(ai): ChangeFilter mit Speicherregeln und Entprellung"
```

---

### Task 3: Datenbank, Schema und View `manual_actions`

**Files:**
- Create: `ai/sql/schema.sql`, `ai/collector/db.py`
- Test: `ai/tests/test_db.py`, `ai/tests/test_manual_actions.py`
- Modify: `ai/tests/conftest.py` (Fixture `db` ergänzen)

**Interfaces:**
- Consumes: `Change`, `EntityRule`, `to_utc_iso`, `parse_utc`, `attrs_json`, `attrs_hash` aus Aufgabe 1.
- Produces: `class Database(path)` mit
  - `close()`, `commit()`, `rollback()`
  - `upsert_entity(rule: EntityRule, room: str | None, now: datetime) -> str` (liefert `"new" | "reactivated" | "unchanged"`)
  - `mark_inactive(entity_id: str, until: datetime) -> None`
  - `sync_room(entity_id, room, role, now) -> None`
  - `add_history_room(room, entity_id, role, valid_from: str | None, valid_to: str | None) -> None`
  - `deactivate_missing(active_ids: set[str], now: datetime) -> list[str]`
  - `insert_changes(changes: Iterable[Change], source: str, commit: bool = True) -> int` (Anzahl neu eingefügter Zeilen)
  - `last_change(entity_id) -> Change | None`, `last_ts(entity_id) -> datetime | None`
  - `active_entity_ids() -> set[str]`
  - `upsert_user(user_id, person_entity, name) -> None`
  - `record_run(source, from_ts, to_ts, rows, status, started_at) -> None`
  - `count_changes(entity_id) -> int`, `purge_entity(entity_id) -> int`
  - `backup_to(path) -> None`
  - SQL-Tabellen `entities, room_map, users, state_changes, ingest_runs` und View `manual_actions` (Spalten `ts_utc, domain, room, entity_id, changed_attrs, old, new, actor_class, actor_user_id`).
  - Fixture `db` (`Database(":memory:")`).

- [ ] **Step 1: Schema schreiben**

`ai/sql/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS entities(
  entity_id     TEXT PRIMARY KEY,
  domain        TEXT NOT NULL,                 -- heating|lighting|shared
  kind          TEXT NOT NULL,
  room          TEXT,
  tracked_attrs TEXT,                          -- JSON-Liste der gespeicherten Attribute
  min_delta     REAL,
  heartbeat_s   INTEGER,
  debounce_s    INTEGER,
  tracked_since TEXT,                          -- ISO-8601 UTC, ab hier liegen Daten vor
  active        INTEGER NOT NULL DEFAULT 1,
  tracked_until TEXT
);

CREATE TABLE IF NOT EXISTS room_map(
  room        TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  role        TEXT NOT NULL,
  valid_from  TEXT NOT NULL DEFAULT '',        -- '' = seit jeher
  valid_to    TEXT,                            -- NULL = noch gültig
  UNIQUE(entity_id, room, role, valid_from)
);

CREATE TABLE IF NOT EXISTS users(
  user_id       TEXT PRIMARY KEY,
  person_entity TEXT,
  name          TEXT
);

CREATE TABLE IF NOT EXISTS state_changes(
  id                INTEGER PRIMARY KEY,
  ts_utc            TEXT NOT NULL,
  entity_id         TEXT NOT NULL,
  state             TEXT,
  attrs             TEXT NOT NULL DEFAULT '{}',
  attrs_hash        TEXT NOT NULL,
  context_id        TEXT,
  context_user_id   TEXT,
  context_parent_id TEXT,
  source            TEXT NOT NULL,
  UNIQUE(entity_id, ts_utc, state, attrs_hash)
);
CREATE INDEX IF NOT EXISTS idx_sc_entity_ts ON state_changes(entity_id, ts_utc);
CREATE INDEX IF NOT EXISTS idx_sc_user ON state_changes(context_user_id);

CREATE TABLE IF NOT EXISTS ingest_runs(
  id         INTEGER PRIMARY KEY,
  source     TEXT NOT NULL,
  from_ts    TEXT,
  to_ts      TEXT,
  rows       INTEGER NOT NULL,
  status     TEXT NOT NULL,
  started_at TEXT NOT NULL
);

DROP VIEW IF EXISTS manual_actions;
CREATE VIEW manual_actions AS
WITH base AS (
  SELECT sc.ts_utc, sc.entity_id, e.domain, e.room, sc.state AS new_state,
         json_extract(sc.attrs, '$.temperature') AS new_temp,
         json_extract(sc.attrs, '$.preset_mode') AS new_preset,
         LAG(sc.state) OVER w AS old_state,
         LAG(json_extract(sc.attrs, '$.temperature')) OVER w AS old_temp,
         LAG(json_extract(sc.attrs, '$.preset_mode')) OVER w AS old_preset,
         sc.context_user_id, sc.context_parent_id
  FROM state_changes sc
  JOIN entities e ON e.entity_id = sc.entity_id
  WHERE e.kind = 'climate'
  WINDOW w AS (PARTITION BY sc.entity_id ORDER BY sc.ts_utc, sc.id)
), diff AS (
  SELECT *,
    CASE WHEN old_state NOT IN ('unavailable', 'unknown') AND new_state NOT IN ('unavailable', 'unknown') THEN
      TRIM(
        (CASE WHEN old_temp IS NOT new_temp THEN ',temperature' ELSE '' END) ||
        (CASE WHEN old_preset IS NOT new_preset THEN ',preset_mode' ELSE '' END) ||
        (CASE WHEN old_state IS NOT new_state THEN ',hvac_mode' ELSE '' END), ',')
    ELSE '' END AS changed
  FROM base
  WHERE old_state IS NOT NULL
)
SELECT ts_utc, domain, room, entity_id, changed AS changed_attrs,
       json_object('temperature', old_temp, 'preset_mode', old_preset, 'hvac_mode', old_state) AS old,
       json_object('temperature', new_temp, 'preset_mode', new_preset, 'hvac_mode', new_state) AS new,
       CASE WHEN context_parent_id IS NOT NULL THEN 'automation'
            WHEN context_user_id IS NOT NULL THEN 'manual'
            ELSE 'unknown' END AS actor_class,
       context_user_id AS actor_user_id
FROM diff
WHERE changed <> '';
```

- [ ] **Step 2: Fixture und Tests für die Datenbank schreiben**

An `ai/tests/conftest.py` anhängen:

```python
from collector.db import Database  # noqa: E402


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()
```

`ai/tests/test_db.py`:

```python
from datetime import datetime, timedelta, timezone

from collector.db import Database
from collector.models import Change, EntityRule

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def rule(entity_id="climate.bt", kind="climate", domain="heating") -> EntityRule:
    return EntityRule(entity_id=entity_id, kind=kind, domain=domain, attrs=("temperature",))


def change(seconds, state="heat", entity_id="climate.bt", **attrs) -> Change:
    return Change(T0 + timedelta(seconds=seconds), entity_id, state, attrs)


def test_upsert_entity_reports_new_unchanged_and_reactivated(db: Database):
    assert db.upsert_entity(rule(), "buero", T0) == "new"
    assert db.upsert_entity(rule(), "buero", T0) == "unchanged"
    db.deactivate_missing(set(), T0 + timedelta(hours=1))
    assert db.upsert_entity(rule(), "buero", T0 + timedelta(hours=2)) == "reactivated"
    row = db.conn.execute("SELECT active, tracked_until FROM entities WHERE entity_id='climate.bt'").fetchone()
    assert (row["active"], row["tracked_until"]) == (1, None)


def test_deactivate_missing_keeps_rows_and_sets_tracked_until(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    db.insert_changes([change(0, temperature=17)], "live")
    gone = db.deactivate_missing(set(), T0 + timedelta(hours=1))
    assert gone == ["climate.bt"]
    row = db.conn.execute("SELECT active, tracked_until FROM entities").fetchone()
    assert row["active"] == 0
    assert row["tracked_until"] == "2026-01-01T11:00:00.000000+00:00"
    assert db.count_changes("climate.bt") == 1


def test_insert_changes_is_idempotent_and_counts_new_rows(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    rows = [change(0, temperature=17), change(10, temperature=21)]
    assert db.insert_changes(rows, "live") == 2
    assert db.insert_changes(rows, "backfill:a.db") == 0
    assert db.count_changes("climate.bt") == 2


def test_insert_changes_lowers_tracked_since(db: Database):
    db.upsert_entity(rule(), "buero", T0 + timedelta(days=10))
    db.insert_changes([change(0, temperature=17)], "backfill:a.db")
    row = db.conn.execute("SELECT tracked_since FROM entities").fetchone()
    assert row["tracked_since"] == "2026-01-01T10:00:00.000000+00:00"


def test_insert_changes_stores_unavailable_without_attrs(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    db.insert_changes([Change(T0, "climate.bt", "unavailable", {})], "live")
    row = db.conn.execute("SELECT state, attrs FROM state_changes").fetchone()
    assert (row["state"], row["attrs"]) == ("unavailable", "{}")


def test_insert_changes_tolerates_non_json_attribute_values(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    odd = Change(T0, "climate.bt", "heat", {"when": T0, "nested": {"a": [1, 2]}})
    assert db.insert_changes([odd], "live") == 1


def test_last_change_and_last_ts_return_newest(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    db.insert_changes([change(0, temperature=17), change(30, temperature=21), change(10, temperature=19)], "live")
    last = db.last_change("climate.bt")
    assert last.attrs == {"temperature": 21}
    assert last.ts == T0 + timedelta(seconds=30)
    assert db.last_ts("climate.bt") == T0 + timedelta(seconds=30)
    assert db.last_ts("climate.nope") is None
    assert db.last_change("climate.nope") is None


def test_insert_without_commit_can_be_rolled_back(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    db.insert_changes([change(0, temperature=17)], "backfill:a.db", commit=False)
    db.rollback()
    assert db.count_changes("climate.bt") == 0


def test_sync_room_closes_old_row_when_area_changes(db: Database):
    db.sync_room("climate.bt", "buero", "climate", T0)
    db.sync_room("climate.bt", "buero", "climate", T0 + timedelta(hours=1))   # keine Änderung
    db.sync_room("climate.bt", "flur", "climate", T0 + timedelta(hours=2))
    rows = db.conn.execute("SELECT room, valid_from, valid_to FROM room_map ORDER BY valid_from").fetchall()
    assert [(r["room"], r["valid_to"] is None) for r in rows] == [("buero", False), ("flur", True)]


def test_add_history_room_is_idempotent(db: Database):
    for _ in range(2):
        db.add_history_room("buero", "climate.trv_office", "climate", None, "2025-12-01T00:00:00+00:00")
    assert db.conn.execute("SELECT COUNT(*) FROM room_map").fetchone()[0] == 1


def test_purge_entity_removes_all_rows(db: Database):
    db.upsert_entity(rule(), "buero", T0)
    db.sync_room("climate.bt", "buero", "climate", T0)
    db.insert_changes([change(0, temperature=17), change(5, temperature=18)], "live")
    assert db.purge_entity("climate.bt") == 2
    assert db.count_changes("climate.bt") == 0
    assert db.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM room_map").fetchone()[0] == 0


def test_mark_inactive_sets_tracked_until(db: Database):
    db.upsert_entity(rule("climate.trv_office"), "buero", T0)
    db.mark_inactive("climate.trv_office", T0 + timedelta(days=5))
    row = db.conn.execute("SELECT active, tracked_until FROM entities").fetchone()
    assert (row["active"], row["tracked_until"]) == (0, "2026-01-06T10:00:00.000000+00:00")


def test_backup_to_creates_readable_copy(db: Database, tmp_path):
    db.upsert_entity(rule(), "buero", T0)
    db.insert_changes([change(0, temperature=17)], "live")
    target = tmp_path / "sub" / "copy.sqlite"
    db.backup_to(target)
    copy = Database(target)
    assert copy.count_changes("climate.bt") == 1
    copy.close()


def test_record_run_and_users(db: Database):
    db.record_run("backfill:a.db", "a", "b", 5, "ok", "c")
    db.upsert_user("u1", "person.fabian", "Fabian")
    db.upsert_user("u1", "person.fabian", "Fabian W.")
    assert db.conn.execute("SELECT rows, status FROM ingest_runs").fetchone()["rows"] == 5
    assert db.conn.execute("SELECT name FROM users WHERE user_id='u1'").fetchone()["name"] == "Fabian W."


def test_active_entity_ids(db: Database):
    db.upsert_entity(rule("climate.a"), None, T0)
    db.upsert_entity(rule("climate.b"), None, T0)
    db.deactivate_missing({"climate.a"}, T0)
    assert db.active_entity_ids() == {"climate.a"}
```

`ai/tests/test_manual_actions.py`:

```python
import json
from datetime import datetime, timedelta, timezone

from collector.db import Database
from collector.models import Change, EntityRule

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
CLIMATE = EntityRule(entity_id="climate.bt", kind="climate", domain="heating", attrs=("temperature", "preset_mode"))


def add(db, seconds, state, temperature=None, preset=None, user=None, parent=None, entity="climate.bt"):
    attrs = {} if temperature is None else {"temperature": temperature, "preset_mode": preset}
    db.insert_changes(
        [Change(T0 + timedelta(seconds=seconds), entity, state, attrs, f"c{seconds}", user, parent)], "live"
    )


def rows(db):
    return db.conn.execute(
        "SELECT ts_utc, changed_attrs, actor_class, actor_user_id, old, new FROM manual_actions ORDER BY ts_utc"
    ).fetchall()


def test_user_change_without_parent_is_manual(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 17, "none")
    add(db, 60, "heat", 21, "none", user="u1")
    (row,) = rows(db)
    assert row["changed_attrs"] == "temperature"
    assert (row["actor_class"], row["actor_user_id"]) == ("manual", "u1")
    assert json.loads(row["old"])["temperature"] == 17
    assert json.loads(row["new"])["temperature"] == 21


def test_change_with_parent_context_is_automation(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 21, "none")
    add(db, 60, "heat", 21, "comfort", parent="c0")
    (row,) = rows(db)
    assert (row["changed_attrs"], row["actor_class"]) == ("preset_mode", "automation")


def test_change_without_user_and_parent_is_unknown(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 17, "none")
    add(db, 60, "heat", 18, "none")
    (row,) = rows(db)
    assert row["actor_class"] == "unknown"


def test_user_and_parent_together_count_as_automation(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 17, "none")
    add(db, 60, "heat", 19, "none", user="u1", parent="cX")
    assert rows(db)[0]["actor_class"] == "automation"


def test_hvac_mode_change_is_reported(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 21, "comfort")
    add(db, 60, "off", 21, "comfort", user="u2")
    (row,) = rows(db)
    assert (row["changed_attrs"], row["actor_class"]) == ("hvac_mode", "manual")


def test_unavailable_transitions_are_not_reported(db: Database):
    db.upsert_entity(CLIMATE, "buero", T0)
    add(db, 0, "heat", 21, "none")
    add(db, 10, "unavailable")
    add(db, 20, "heat", 21, "none")
    assert rows(db) == []


def test_only_climate_entities_are_considered(db: Database):
    flat = EntityRule(entity_id="input_boolean.x", kind="flat", domain="shared")
    db.upsert_entity(flat, None, T0)
    add(db, 0, "off", entity="input_boolean.x")
    add(db, 10, "on", entity="input_boolean.x", user="u1")
    assert rows(db) == []


def test_entities_are_compared_separately(db: Database):
    other = EntityRule(entity_id="climate.other", kind="climate", domain="heating", attrs=("temperature", "preset_mode"))
    db.upsert_entity(CLIMATE, "buero", T0)
    db.upsert_entity(other, "bad", T0)
    add(db, 0, "heat", 17, "none")
    add(db, 5, "heat", 30, "none", entity="climate.other")
    add(db, 10, "heat", 17, "none", entity="climate.other")
    assert [r["changed_attrs"] for r in rows(db)] == ["temperature"]
```

- [ ] **Step 3: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_db.py tests/test_manual_actions.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.db'`.

- [ ] **Step 4: Datenbankschicht implementieren**

`ai/collector/db.py`:

```python
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import Change, EntityRule, attrs_hash, attrs_json, parse_utc, to_utc_iso

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


class Database:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # --- Entitäten ---------------------------------------------------------

    def upsert_entity(self, rule: EntityRule, room: str | None, now: datetime) -> str:
        row = self.conn.execute("SELECT active FROM entities WHERE entity_id = ?", (rule.entity_id,)).fetchone()
        status = "new" if row is None else ("reactivated" if row["active"] == 0 else "unchanged")
        self.conn.execute(
            """
            INSERT INTO entities(entity_id, domain, kind, room, tracked_attrs, min_delta, heartbeat_s,
                                 debounce_s, tracked_since, active, tracked_until)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)
            ON CONFLICT(entity_id) DO UPDATE SET
              domain = excluded.domain, kind = excluded.kind, room = excluded.room,
              tracked_attrs = excluded.tracked_attrs, min_delta = excluded.min_delta,
              heartbeat_s = excluded.heartbeat_s, debounce_s = excluded.debounce_s,
              active = 1, tracked_until = NULL
            """,
            (rule.entity_id, rule.domain, rule.kind, room, json.dumps(list(rule.attrs)),
             rule.numeric_state_delta, rule.heartbeat_s, rule.debounce_s, to_utc_iso(now)),
        )
        self.conn.commit()
        return status

    def mark_inactive(self, entity_id: str, until: datetime) -> None:
        self.conn.execute(
            "UPDATE entities SET active = 0, tracked_until = ? WHERE entity_id = ?",
            (to_utc_iso(until), entity_id),
        )
        self.conn.commit()

    def deactivate_missing(self, active_ids: set[str], now: datetime) -> list[str]:
        rows = self.conn.execute("SELECT entity_id FROM entities WHERE active = 1").fetchall()
        gone = [row["entity_id"] for row in rows if row["entity_id"] not in active_ids]
        for entity_id in gone:
            self.conn.execute(
                "UPDATE entities SET active = 0, tracked_until = ? WHERE entity_id = ?",
                (to_utc_iso(now), entity_id),
            )
        self.conn.commit()
        return gone

    def active_entity_ids(self) -> set[str]:
        rows = self.conn.execute("SELECT entity_id FROM entities WHERE active = 1").fetchall()
        return {row["entity_id"] for row in rows}

    # --- Räume und Benutzer -------------------------------------------------

    def sync_room(self, entity_id: str, room: str | None, role: str, now: datetime) -> None:
        if room is None:
            return
        current = self.conn.execute(
            "SELECT room, role FROM room_map WHERE entity_id = ? AND valid_to IS NULL", (entity_id,)
        ).fetchone()
        if current is not None and current["room"] == room and current["role"] == role:
            return
        stamp = to_utc_iso(now)
        if current is not None:
            self.conn.execute(
                "UPDATE room_map SET valid_to = ? WHERE entity_id = ? AND valid_to IS NULL", (stamp, entity_id)
            )
        self.conn.execute(
            "INSERT OR IGNORE INTO room_map(room, entity_id, role, valid_from, valid_to) VALUES (?, ?, ?, ?, NULL)",
            (room, entity_id, role, stamp),
        )
        self.conn.commit()

    def add_history_room(self, room: str, entity_id: str, role: str,
                         valid_from: str | None, valid_to: str | None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO room_map(room, entity_id, role, valid_from, valid_to) VALUES (?, ?, ?, ?, ?)",
            (room, entity_id, role, valid_from or "", valid_to),
        )
        self.conn.commit()

    def upsert_user(self, user_id: str, person_entity: str | None, name: str | None) -> None:
        self.conn.execute(
            """
            INSERT INTO users(user_id, person_entity, name) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET person_entity = excluded.person_entity, name = excluded.name
            """,
            (user_id, person_entity, name),
        )
        self.conn.commit()

    # --- Zustandsänderungen -------------------------------------------------

    def insert_changes(self, changes: Iterable[Change], source: str, commit: bool = True) -> int:
        rows = []
        first_seen: dict[str, str] = {}
        for change in changes:
            stamp = to_utc_iso(change.ts)
            rows.append((stamp, change.entity_id, change.state, attrs_json(change.attrs),
                         attrs_hash(change.attrs), change.context_id, change.context_user_id,
                         change.context_parent_id, source))
            if change.entity_id not in first_seen or stamp < first_seen[change.entity_id]:
                first_seen[change.entity_id] = stamp
        if not rows:
            return 0
        before = self.conn.total_changes
        try:
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO state_changes(ts_utc, entity_id, state, attrs, attrs_hash,
                  context_id, context_user_id, context_parent_id, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            inserted = self.conn.total_changes - before
            for entity_id, stamp in first_seen.items():
                self.conn.execute(
                    "UPDATE entities SET tracked_since = ? "
                    "WHERE entity_id = ? AND (tracked_since IS NULL OR tracked_since > ?)",
                    (stamp, entity_id, stamp),
                )
            if commit:
                self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return inserted

    def last_change(self, entity_id: str) -> Change | None:
        row = self.conn.execute(
            "SELECT * FROM state_changes WHERE entity_id = ? ORDER BY ts_utc DESC, id DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
        if row is None:
            return None
        return Change(parse_utc(row["ts_utc"]), row["entity_id"], row["state"],
                      json.loads(row["attrs"] or "{}"), row["context_id"],
                      row["context_user_id"], row["context_parent_id"])

    def last_ts(self, entity_id: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(ts_utc) AS ts FROM state_changes WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        return parse_utc(row["ts"]) if row["ts"] else None

    def count_changes(self, entity_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM state_changes WHERE entity_id = ?", (entity_id,)
        ).fetchone()[0]

    # --- Verwaltung ------------------------------------------------------------

    def record_run(self, source: str, from_ts: str | None, to_ts: str | None,
                   rows: int, status: str, started_at: str) -> None:
        self.conn.execute(
            "INSERT INTO ingest_runs(source, from_ts, to_ts, rows, status, started_at) VALUES (?, ?, ?, ?, ?, ?)",
            (source, from_ts, to_ts, rows, status, started_at),
        )
        self.conn.commit()

    def purge_entity(self, entity_id: str) -> int:
        deleted = self.count_changes(entity_id)
        self.conn.execute("DELETE FROM state_changes WHERE entity_id = ?", (entity_id,))
        self.conn.execute("DELETE FROM room_map WHERE entity_id = ?", (entity_id,))
        self.conn.execute("DELETE FROM entities WHERE entity_id = ?", (entity_id,))
        self.conn.commit()
        return deleted

    def backup_to(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        destination = sqlite3.connect(str(temporary))
        try:
            self.conn.backup(destination)
        finally:
            destination.close()
        temporary.replace(target)
```

- [ ] **Step 5: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_db.py tests/test_manual_actions.py -v`
Expected: PASS (23 Tests).

- [ ] **Step 6: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle bisherigen Tests PASS.

```bash
git add ai/sql ai/collector/db.py ai/tests
git commit -m "feat(ai): SQLite-Schema, Datenbankschicht und View manual_actions"
```

### Task 4: Home-Assistant-WebSocket-Client

**Files:**
- Create: `ai/collector/hass_ws.py`, `ai/tests/fake_hass.py`, `ai/tests/test_hass_ws.py`
- Modify: `ai/tests/conftest.py` (Fixture `fake_hass` ergänzen)

**Interfaces:**
- Produces:
  - `class HassConnectionError(Exception)`, `class HassAuthError(Exception)`, `class HassCommandError(Exception)`
  - `class HassClient(url: str, token: str)` mit `async connect()`, `async close()`, `async command(type_: str, **payload) -> Any`, `async subscribe(event_type: str) -> AsyncIterator[dict]` (liefert ein asynchrones Iterator-Objekt; beim Verbindungsende löst das nächste `__anext__` `HassConnectionError` aus), `seconds_since_last_message() -> float`.
  - Test-Attrappe `FakeHass` mit Attributen `url`, `token`, `responses: dict[str, Any | Callable[[dict], Any]]`, `events: list[dict]`, `batch_events: bool`, `close_after_events: bool`, `close_after_command: str | None`, `received: list[dict]`; Hilfsfunktion `set_registry(fake, entities, labels=None, devices=None)`.
  - Fixture `fake_hass` (laufender Test-Server auf `fake_hass.url`, Token `"secret"`).

- [ ] **Step 1: Test-Attrappe und Fixture schreiben**

`ai/tests/fake_hass.py`:

```python
from __future__ import annotations

import json
from typing import Any

from aiohttp import web

DEFAULT_LABELS = [{"label_id": "ki_db_sync", "name": "KI_DB_Sync"}]


class FakeHass:
    """Kleine Attrappe der Home-Assistant-WebSocket-API für Tests."""

    def __init__(self, token: str = "secret"):
        self.token = token
        self.url = ""
        self.responses: dict[str, Any] = {}
        self.events: list[dict] = []
        self.batch_events = False
        self.close_after_events = False
        self.close_after_command: str | None = None
        self.received: list[dict] = []

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        auth = await ws.receive_json()
        if auth.get("access_token") != self.token:
            await ws.send_json({"type": "auth_invalid", "message": "bad token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})
        async for raw in ws:
            msg = json.loads(raw.data)
            self.received.append(msg)
            if msg["type"] == "subscribe_events":
                await ws.send_json({"id": msg["id"], "type": "result", "success": True, "result": None})
                if self.batch_events and self.events:
                    await ws.send_json(
                        [{"id": msg["id"], "type": "event", "event": event} for event in self.events]
                    )
                else:
                    for event in self.events:
                        await ws.send_json({"id": msg["id"], "type": "event", "event": event})
                if self.close_after_events:
                    await ws.close()
                continue
            if msg["type"] not in self.responses:
                await ws.send_json({"id": msg["id"], "type": "result", "success": False,
                                    "error": {"code": "unknown_command", "message": msg["type"]}})
                continue
            result = self.responses[msg["type"]]
            if callable(result):
                result = result(msg)
            await ws.send_json({"id": msg["id"], "type": "result", "success": True, "result": result})
            if self.close_after_command == msg["type"]:
                await ws.close()
        return ws


def set_registry(fake: FakeHass, entities: list[dict], labels: list[dict] | None = None,
                 devices: list[dict] | None = None) -> None:
    fake.responses["config/label_registry/list"] = DEFAULT_LABELS if labels is None else labels
    fake.responses["config/entity_registry/list"] = entities
    fake.responses["config/device_registry/list"] = devices or []
```

An `ai/tests/conftest.py` anhängen:

```python
import pytest_asyncio  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestServer  # noqa: E402

from tests.fake_hass import FakeHass  # noqa: E402


@pytest_asyncio.fixture
async def fake_hass():
    fake = FakeHass()
    app = web.Application()
    app.router.add_get("/api/websocket", fake.handler)
    server = TestServer(app)
    await server.start_server()
    fake.url = f"ws://127.0.0.1:{server.port}/api/websocket"
    try:
        yield fake
    finally:
        await server.close()
```

- [ ] **Step 2: Tests schreiben**

`ai/tests/test_hass_ws.py`:

```python
import pytest

from collector.hass_ws import HassAuthError, HassClient, HassCommandError, HassConnectionError


async def connected(fake) -> HassClient:
    client = HassClient(fake.url, "secret")
    await client.connect()
    return client


async def test_command_roundtrip(fake_hass):
    fake_hass.responses["demo/ping"] = {"pong": 1}
    client = await connected(fake_hass)
    try:
        assert await client.command("demo/ping") == {"pong": 1}
        assert fake_hass.received[-1]["type"] == "demo/ping"
    finally:
        await client.close()


async def test_wrong_token_raises_auth_error(fake_hass):
    client = HassClient(fake_hass.url, "wrong")
    with pytest.raises(HassAuthError):
        await client.connect()


async def test_unreachable_server_raises_connection_error():
    client = HassClient("ws://127.0.0.1:9/api/websocket", "x")
    with pytest.raises(HassConnectionError):
        await client.connect()


async def test_failed_command_raises_command_error(fake_hass):
    client = await connected(fake_hass)
    try:
        with pytest.raises(HassCommandError, match="unknown_command"):
            await client.command("demo/unknown")
    finally:
        await client.close()


async def test_subscribe_yields_events_in_order(fake_hass):
    fake_hass.events = [{"n": 1}, {"n": 2}]
    client = await connected(fake_hass)
    try:
        stream = await client.subscribe("state_changed")
        assert [await stream.__anext__(), await stream.__anext__()] == [{"n": 1}, {"n": 2}]
    finally:
        await client.close()


async def test_batched_messages_are_dispatched(fake_hass):
    fake_hass.events = [{"n": 1}, {"n": 2}, {"n": 3}]
    fake_hass.batch_events = True
    client = await connected(fake_hass)
    try:
        stream = await client.subscribe("state_changed")
        assert [await stream.__anext__() for _ in range(3)] == [{"n": 1}, {"n": 2}, {"n": 3}]
    finally:
        await client.close()


async def test_closed_connection_ends_stream_with_error(fake_hass):
    fake_hass.events = [{"n": 1}]
    fake_hass.close_after_events = True
    client = await connected(fake_hass)
    try:
        stream = await client.subscribe("state_changed")
        assert await stream.__anext__() == {"n": 1}
        with pytest.raises(HassConnectionError):
            await stream.__anext__()
        with pytest.raises(HassConnectionError):
            await client.command("demo/ping")
    finally:
        await client.close()


async def test_seconds_since_last_message_is_small_after_traffic(fake_hass):
    fake_hass.responses["demo/ping"] = None
    client = await connected(fake_hass)
    try:
        await client.command("demo/ping")
        assert client.seconds_since_last_message() < 2
    finally:
        await client.close()
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_hass_ws.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.hass_ws'`.

- [ ] **Step 4: Client implementieren**

`ai/collector/hass_ws.py`:

```python
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

import aiohttp


class HassConnectionError(Exception):
    pass


class HassAuthError(Exception):
    pass


class HassCommandError(Exception):
    pass


class HassClient:
    """Minimaler Client für die Home-Assistant-WebSocket-API."""

    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._subs: dict[int, asyncio.Queue] = {}
        self._last_message = time.monotonic()

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(self.url, heartbeat=30, max_msg_size=0)
            hello = await self._ws.receive_json()
            if hello.get("type") != "auth_required":
                raise HassConnectionError(f"unerwartete Begrüßung: {hello}")
            await self._ws.send_json({"type": "auth", "access_token": self.token})
            answer = await self._ws.receive_json()
        except HassConnectionError:
            await self.close()
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError) as exc:
            await self.close()
            raise HassConnectionError(str(exc)) from exc
        if answer.get("type") == "auth_invalid":
            await self.close()
            raise HassAuthError(answer.get("message", "auth_invalid"))
        if answer.get("type") != "auth_ok":
            await self.close()
            raise HassConnectionError(f"unerwartete Antwort: {answer}")
        self._last_message = time.monotonic()
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()
            self._session = None

    def seconds_since_last_message(self) -> float:
        return time.monotonic() - self._last_message

    async def command(self, type_: str, **payload: Any) -> Any:
        message = await self._request({"type": type_, **payload})
        if not message.get("success"):
            error = message.get("error") or {}
            raise HassCommandError(f"{type_}: {error.get('code')} {error.get('message')}")
        return message.get("result")

    async def subscribe(self, event_type: str) -> AsyncIterator[dict]:
        if self._ws is None or self._ws.closed:
            raise HassConnectionError("nicht verbunden")
        queue: asyncio.Queue = asyncio.Queue()
        message_id = self._allocate_id()
        self._subs[message_id] = queue
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self._send({"id": message_id, "type": "subscribe_events", "event_type": event_type})
        answer = await future
        if not answer.get("success"):
            raise HassCommandError(f"subscribe_events: {answer.get('error')}")
        return self._iterate(queue)

    async def _iterate(self, queue: asyncio.Queue) -> AsyncIterator[dict]:
        while True:
            item = await queue.get()
            if item is None:
                raise HassConnectionError("Verbindung geschlossen")
            yield item

    def _allocate_id(self) -> int:
        message_id = self._next_id
        self._next_id += 1
        return message_id

    async def _send(self, payload: dict) -> None:
        if self._ws is None or self._ws.closed:
            raise HassConnectionError("nicht verbunden")
        try:
            await self._ws.send_json(payload)
        except (aiohttp.ClientError, ConnectionResetError) as exc:
            raise HassConnectionError(str(exc)) from exc

    async def _request(self, payload: dict) -> dict:
        if self._ws is None or self._ws.closed:
            raise HassConnectionError("nicht verbunden")
        message_id = self._allocate_id()
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            await self._send({"id": message_id, **payload})
        except HassConnectionError:
            self._pending.pop(message_id, None)
            raise
        return await future

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    continue
                self._last_message = time.monotonic()
                data = json.loads(raw.data)
                for message in data if isinstance(data, list) else [data]:
                    self._dispatch(message)
        except (aiohttp.ClientError, ValueError):
            pass
        finally:
            error = HassConnectionError("Verbindung geschlossen")
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            self._pending.clear()
            for queue in self._subs.values():
                queue.put_nowait(None)

    def _dispatch(self, message: dict) -> None:
        kind, message_id = message.get("type"), message.get("id")
        if kind == "result":
            future = self._pending.pop(message_id, None)
            if future is not None and not future.done():
                future.set_result(message)
        elif kind == "event":
            queue = self._subs.get(message_id)
            if queue is not None:
                queue.put_nowait(message.get("event"))
```

- [ ] **Step 5: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_hass_ws.py -v`
Expected: PASS (8 Tests).

- [ ] **Step 6: Commit**

```bash
git add ai/collector/hass_ws.py ai/tests/fake_hass.py ai/tests/test_hass_ws.py ai/tests/conftest.py
git commit -m "feat(ai): WebSocket-Client für Home Assistant mit Test-Attrappe"
```

---

### Task 5: Label-Abgleich mit der Entity Registry

**Files:**
- Create: `ai/collector/registry.py`
- Test: `ai/tests/test_registry.py`

**Interfaces:**
- Consumes: `HassClient.command(...)` (Aufgabe 4), `EntityInfo` (Aufgabe 1), `FakeHass`, `set_registry` (Aufgabe 4).
- Produces:
  - `class LabelNotFound(Exception)`, `class LabelEmpty(Exception)`
  - `@dataclass(frozen=True) class UserInfo(user_id: str, person_entity: str, name: str | None)`
  - `async fetch_labeled_entities(client, label_name: str) -> list[EntityInfo]` (sortiert nach `entity_id`; `area` ist die `area_id` der Entität oder ersatzweise des Geräts; `device_class` aus `device_class` oder `original_device_class`; deaktivierte Entitäten fehlen)
  - `async fetch_users(client) -> list[UserInfo]`

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_registry.py`:

```python
import pytest

from collector.hass_ws import HassClient
from collector.registry import LabelEmpty, LabelNotFound, fetch_labeled_entities, fetch_users
from tests.fake_hass import set_registry


def entry(entity_id, labels=("ki_db_sync",), area_id=None, device_id=None,
          device_class=None, original_device_class=None, disabled_by=None):
    return {"entity_id": entity_id, "labels": list(labels), "area_id": area_id, "device_id": device_id,
            "device_class": device_class, "original_device_class": original_device_class,
            "disabled_by": disabled_by}


async def connect(fake) -> HassClient:
    client = HassClient(fake.url, "secret")
    await client.connect()
    return client


async def test_returns_only_labeled_enabled_entities_sorted(fake_hass):
    set_registry(fake_hass, [
        entry("sensor.b", area_id="buero", device_class="temperature"),
        entry("sensor.a", area_id="bad"),
        entry("sensor.other", labels=("andere",)),
        entry("sensor.off", disabled_by="user"),
    ])
    client = await connect(fake_hass)
    try:
        infos = await fetch_labeled_entities(client, "KI_DB_Sync")
    finally:
        await client.close()
    assert [(i.entity_id, i.area, i.device_class) for i in infos] == [
        ("sensor.a", "bad", None),
        ("sensor.b", "buero", "temperature"),
    ]


async def test_area_falls_back_to_device_area_and_device_class_to_original(fake_hass):
    set_registry(
        fake_hass,
        [entry("sensor.x", device_id="dev1", original_device_class="humidity")],
        devices=[{"id": "dev1", "area_id": "wohnzimmer"}],
    )
    client = await connect(fake_hass)
    try:
        (info,) = await fetch_labeled_entities(client, "KI_DB_Sync")
    finally:
        await client.close()
    assert (info.area, info.device_class) == ("wohnzimmer", "humidity")


async def test_entity_area_beats_device_area(fake_hass):
    set_registry(
        fake_hass,
        [entry("sensor.x", area_id="bad", device_id="dev1")],
        devices=[{"id": "dev1", "area_id": "wohnzimmer"}],
    )
    client = await connect(fake_hass)
    try:
        (info,) = await fetch_labeled_entities(client, "KI_DB_Sync")
    finally:
        await client.close()
    assert info.area == "bad"


async def test_missing_label_raises(fake_hass):
    set_registry(fake_hass, [entry("sensor.x")], labels=[])
    client = await connect(fake_hass)
    try:
        with pytest.raises(LabelNotFound, match="KI_DB_Sync"):
            await fetch_labeled_entities(client, "KI_DB_Sync")
    finally:
        await client.close()


async def test_label_without_entities_raises(fake_hass):
    set_registry(fake_hass, [entry("sensor.x", labels=())])
    client = await connect(fake_hass)
    try:
        with pytest.raises(LabelEmpty, match="KI_DB_Sync"):
            await fetch_labeled_entities(client, "KI_DB_Sync")
    finally:
        await client.close()


async def test_fetch_users_reads_user_ids_from_person_entities(fake_hass):
    fake_hass.responses["get_states"] = [
        {"entity_id": "person.fabian", "attributes": {"user_id": "u1", "friendly_name": "Fabian"}},
        {"entity_id": "person.gast", "attributes": {"friendly_name": "Gast"}},
        {"entity_id": "sensor.x", "attributes": {"user_id": "nein"}},
    ]
    client = await connect(fake_hass)
    try:
        users = await fetch_users(client)
    finally:
        await client.close()
    assert [(u.user_id, u.person_entity, u.name) for u in users] == [("u1", "person.fabian", "Fabian")]
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_registry.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.registry'`.

- [ ] **Step 3: Implementieren**

`ai/collector/registry.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from .hass_ws import HassClient
from .models import EntityInfo


class LabelNotFound(Exception):
    pass


class LabelEmpty(Exception):
    pass


@dataclass(frozen=True)
class UserInfo:
    user_id: str
    person_entity: str
    name: str | None


async def fetch_labeled_entities(client: HassClient, label_name: str) -> list[EntityInfo]:
    labels = await client.command("config/label_registry/list")
    label_id = next((label["label_id"] for label in labels if label.get("name") == label_name), None)
    if label_id is None:
        raise LabelNotFound(
            f"Das Label '{label_name}' existiert in Home Assistant nicht. "
            "Lege es an und weise es den Entitäten zu (siehe README)."
        )
    entities = await client.command("config/entity_registry/list")
    devices = await client.command("config/device_registry/list")
    device_area = {device["id"]: device.get("area_id") for device in devices}
    found: list[EntityInfo] = []
    for entry in entities:
        if label_id not in (entry.get("labels") or []) or entry.get("disabled_by"):
            continue
        area = entry.get("area_id") or device_area.get(entry.get("device_id"))
        device_class = entry.get("device_class") or entry.get("original_device_class")
        found.append(EntityInfo(entry["entity_id"], device_class, area))
    if not found:
        raise LabelEmpty(f"Keine aktive Entität trägt das Label '{label_name}'.")
    return sorted(found, key=lambda info: info.entity_id)


async def fetch_users(client: HassClient) -> list[UserInfo]:
    users: list[UserInfo] = []
    for state in await client.command("get_states"):
        if not state["entity_id"].startswith("person."):
            continue
        attributes = state.get("attributes") or {}
        user_id = attributes.get("user_id")
        if user_id:
            users.append(UserInfo(user_id, state["entity_id"], attributes.get("friendly_name")))
    return users
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_registry.py -v`
Expected: PASS (6 Tests).

- [ ] **Step 5: Commit**

```bash
git add ai/collector/registry.py ai/tests/test_registry.py
git commit -m "feat(ai): Label-Abgleich mit Entity Registry"
```

---

### Task 6: Pipeline (Filter je Entität und Schreibpuffer)

**Files:**
- Create: `ai/collector/pipeline.py`
- Test: `ai/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `ChangeFilter` (Aufgabe 2), `Database` (Aufgabe 3), `EntityRule`, `Change` (Aufgabe 1).
- Produces: `class Pipeline(db: Database)` mit `rules: dict[str, EntityRule]`, `buffered: int` (Property), `set_rules(rules)`, `handle(change, source="live")`, `tick(now)`, `flush() -> int`. Filter werden mit der zuletzt gespeicherten Änderung aus der Datenbank initialisiert. `flush()` leert den Puffer erst nach erfolgreichem Schreiben.

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_pipeline.py`:

```python
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from collector.db import Database
from collector.models import Change, EntityRule
from collector.pipeline import Pipeline

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
FLAT = EntityRule(entity_id="input_boolean.x", kind="flat", domain="shared")
ROOMS = EntityRule(entity_id="sensor.room", kind="presence", domain="shared", debounce_s=60)


def flat(seconds, state, source_entity="input_boolean.x") -> Change:
    return Change(T0 + timedelta(seconds=seconds), source_entity, state)


def make(db: Database, *rules: EntityRule) -> Pipeline:
    pipeline = Pipeline(db)
    for rule in rules:
        db.upsert_entity(rule, None, T0)
    pipeline.set_rules({rule.entity_id: rule for rule in rules})
    return pipeline


def test_unknown_entities_are_ignored(db):
    pipeline = make(db, FLAT)
    pipeline.handle(flat(0, "on", "light.nobody_listens"))
    assert pipeline.buffered == 0


def test_handle_buffers_and_flush_writes_with_source(db):
    pipeline = make(db, FLAT)
    pipeline.handle(flat(0, "off"))
    pipeline.handle(flat(1, "off"))
    pipeline.handle(flat(2, "on"), source="gap:history_api")
    assert pipeline.buffered == 2
    assert pipeline.flush() == 2
    assert pipeline.buffered == 0
    rows = db.conn.execute("SELECT state, source FROM state_changes ORDER BY ts_utc").fetchall()
    assert [(r["state"], r["source"]) for r in rows] == [("off", "live"), ("on", "gap:history_api")]


def test_flush_of_empty_buffer_returns_zero(db):
    assert make(db, FLAT).flush() == 0


def test_filter_is_seeded_from_stored_data(db):
    db.upsert_entity(FLAT, None, T0)
    db.insert_changes([flat(0, "on")], "live")
    pipeline = Pipeline(db)
    pipeline.set_rules({FLAT.entity_id: FLAT})
    pipeline.handle(flat(60, "on"))
    assert pipeline.buffered == 0
    pipeline.handle(flat(120, "off"))
    assert pipeline.buffered == 1


def test_set_rules_keeps_state_for_unchanged_rules_and_drops_removed(db):
    pipeline = make(db, FLAT, ROOMS)
    pipeline.handle(flat(0, "on"))
    pipeline.set_rules({FLAT.entity_id: FLAT})            # ROOMS entfernt, FLAT unverändert
    pipeline.handle(flat(5, "on"))                        # gleicher Zustand -> weiterhin verworfen
    pipeline.handle(Change(T0, "sensor.room", "Büro"))     # entfernt -> ignoriert
    assert pipeline.buffered == 1


def test_tick_confirms_debounced_values(db):
    pipeline = make(db, ROOMS)
    pipeline.handle(Change(T0, "sensor.room", "Büro"))
    assert pipeline.buffered == 0
    pipeline.tick(T0 + timedelta(seconds=61))
    assert pipeline.buffered == 1


def test_flush_keeps_buffer_when_write_fails(db, monkeypatch):
    pipeline = make(db, FLAT)
    pipeline.handle(flat(0, "on"))
    original = db.insert_changes

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "insert_changes", broken)
    with pytest.raises(sqlite3.OperationalError):
        pipeline.flush()
    assert pipeline.buffered == 1
    monkeypatch.setattr(db, "insert_changes", original)
    assert pipeline.flush() == 1
    assert pipeline.buffered == 0
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_pipeline.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.pipeline'`.

- [ ] **Step 3: Implementieren**

`ai/collector/pipeline.py`:

```python
from __future__ import annotations

from datetime import datetime

from .db import Database
from .filtering import ChangeFilter
from .models import Change, EntityRule


class Pipeline:
    """Hält pro Entität einen ChangeFilter und puffert gespeicherte Änderungen."""

    def __init__(self, db: Database):
        self.db = db
        self.rules: dict[str, EntityRule] = {}
        self._filters: dict[str, ChangeFilter] = {}
        self._buffer: list[tuple[Change, str]] = []

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def set_rules(self, rules: dict[str, EntityRule]) -> None:
        for entity_id in list(self._filters):
            if entity_id not in rules:
                del self._filters[entity_id]
        for entity_id, rule in rules.items():
            if entity_id not in self._filters or self.rules.get(entity_id) != rule:
                self._filters[entity_id] = ChangeFilter(rule, self.db.last_change(entity_id))
        self.rules = dict(rules)

    def handle(self, change: Change, source: str = "live") -> None:
        change_filter = self._filters.get(change.entity_id)
        if change_filter is None:
            return
        for stored in change_filter.feed(change):
            self._buffer.append((stored, source))

    def tick(self, now: datetime) -> None:
        for change_filter in self._filters.values():
            for stored in change_filter.tick(now):
                self._buffer.append((stored, "live"))

    def flush(self) -> int:
        if not self._buffer:
            return 0
        by_source: dict[str, list[Change]] = {}
        for change, source in self._buffer:
            by_source.setdefault(source, []).append(change)
        inserted = 0
        for source, changes in by_source.items():
            inserted += self.db.insert_changes(changes, source)
        self._buffer.clear()  # erst nach erfolgreichem Schreiben; Duplikate fängt UNIQUE ab
        return inserted
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_pipeline.py -v`
Expected: PASS (7 Tests).

- [ ] **Step 5: Commit**

```bash
git add ai/collector/pipeline.py ai/tests/test_pipeline.py
git commit -m "feat(ai): Pipeline mit Filter je Entität und Schreibpuffer"
```

---

### Task 7: Collector (Live-Sitzung, Lückenabgleich, Perioden)

**Files:**
- Create: `ai/collector/live.py`
- Test: `ai/tests/test_live.py`

**Interfaces:**
- Consumes: `Config`, `Database`, `HassClient` und Fehlerklassen, `Pipeline`, `fetch_labeled_entities`, `fetch_users`, `LabelNotFound`, `LabelEmpty`, `resolve_rule`, `FakeHass`, `set_registry`.
- Produces:
  - `change_from_event(event: dict) -> Change | None`
  - `history_to_changes(entity_id: str, states: list[dict]) -> list[Change]`
  - `seconds_until(hhmm: str, tz_name: str, now: datetime) -> float`
  - `class Collector(cfg, db, client_factory, *, health_file: Path | None = None, now=utcnow, sleep=asyncio.sleep)` mit `async sync_registry(client)`, `async gap_fill(client) -> int`, `async run_session(client)`, `async run()`, `stop()`, Attribut `pipeline`.
  - Modulkonstanten `BACKOFF_START = 1.0`, `BACKOFF_MAX = 60.0`.

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_live.py`:

```python
import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from collector import live
from collector.hass_ws import HassAuthError, HassClient, HassConnectionError
from collector.live import Collector, change_from_event, history_to_changes, seconds_until
from collector.registry import LabelNotFound
from tests.fake_hass import set_registry

HISTORY = "history/history_during_period"


def entity_entry(entity_id, labels=("ki_db_sync",), area_id="buero", device_class=None):
    return {"entity_id": entity_id, "labels": list(labels), "area_id": area_id, "device_id": None,
            "device_class": device_class, "original_device_class": None, "disabled_by": None}


def state_event(entity_id, state, stamp, attrs=None, user=None, parent=None, ctx="01CTX"):
    return {"event_type": "state_changed", "data": {
        "entity_id": entity_id, "old_state": None,
        "new_state": {"entity_id": entity_id, "state": state, "attributes": attrs or {},
                      "last_updated": stamp,
                      "context": {"id": ctx, "parent_id": parent, "user_id": user}}}}


def prepare(fake, entities):
    set_registry(fake, entities)
    fake.responses["get_states"] = [
        {"entity_id": "person.fabian", "attributes": {"user_id": "u-fabian", "friendly_name": "Fabian"}}
    ]
    fake.responses[HISTORY] = {}


def make_collector(cfg, db, fake, **kwargs) -> Collector:
    return Collector(cfg, db, lambda: HassClient(fake.url, "secret"), **kwargs)


# --- reine Funktionen ------------------------------------------------------

def test_change_from_event_parses_context_and_z_timestamp():
    event = state_event("climate.bt", "heat", "2026-01-01T10:00:00Z", {"temperature": 21},
                        user="u1", parent="p1", ctx="c1")
    change = change_from_event(event)
    assert change.entity_id == "climate.bt"
    assert change.ts == datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    assert (change.context_id, change.context_user_id, change.context_parent_id) == ("c1", "u1", "p1")
    assert change.attrs == {"temperature": 21}


def test_change_from_event_ignores_removed_entity():
    assert change_from_event({"data": {"entity_id": "x", "new_state": None}}) is None
    assert change_from_event({}) is None


def test_history_to_changes_converts_epoch_and_skips_incomplete_rows():
    changes = history_to_changes("input_boolean.x", [
        {"s": "on", "a": {"k": 1}, "lu": 1767261600.0},
        {"s": "off", "lc": 1767265200.0},
        {"a": {}},
        {"s": None, "lu": 1767265300.0},
    ])
    assert [(c.state, c.ts.isoformat()) for c in changes] == [
        ("on", "2026-01-01T10:00:00+00:00"),
        ("off", "2026-01-01T11:00:00+00:00"),
    ]
    assert changes[0].attrs == {"k": 1}


def test_seconds_until_waits_for_next_local_time():
    now = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)          # 03:00 in Berlin
    assert seconds_until("03:30", "Europe/Berlin", now) == 1800
    later = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)        # 04:00 in Berlin -> nächster Tag
    assert seconds_until("03:30", "Europe/Berlin", later) == 84600


# --- Registry-Abgleich -------------------------------------------------------

async def test_sync_registry_creates_entities_rules_rooms_and_users(cfg, db, fake_hass):
    prepare(fake_hass, [
        entity_entry("climate.better_thermostat_office", area_id="buero"),
        entity_entry("input_boolean.someone_in_bed", area_id=None),
        entity_entry("sensor.unlabeled", labels=()),
    ])
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    collector = make_collector(cfg, db, fake_hass)
    try:
        await collector.sync_registry(client)
    finally:
        await client.close()
    entities = {r["entity_id"]: r for r in db.conn.execute("SELECT * FROM entities")}
    assert set(entities) == {"climate.better_thermostat_office", "input_boolean.someone_in_bed"}
    assert entities["climate.better_thermostat_office"]["domain"] == "heating"
    assert set(collector.pipeline.rules) == set(entities)
    assert [r["room"] for r in db.conn.execute("SELECT room FROM room_map")] == ["buero"]
    assert db.conn.execute("SELECT name FROM users WHERE user_id='u-fabian'").fetchone()["name"] == "Fabian"


async def test_sync_registry_raises_when_label_is_missing_on_first_sync(cfg, db, fake_hass):
    set_registry(fake_hass, [entity_entry("sensor.x")], labels=[])
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    try:
        with pytest.raises(LabelNotFound):
            await make_collector(cfg, db, fake_hass).sync_registry(client)
    finally:
        await client.close()


async def test_sync_registry_keeps_running_when_label_vanishes_later(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    collector = make_collector(cfg, db, fake_hass)
    try:
        await collector.sync_registry(client)
        set_registry(fake_hass, [entity_entry("input_boolean.someone_in_bed")], labels=[])
        await collector.sync_registry(client)          # kein Fehler, bisherige Entitäten bleiben
    finally:
        await client.close()
    assert db.active_entity_ids() == {"input_boolean.someone_in_bed"}
    assert set(collector.pipeline.rules) == {"input_boolean.someone_in_bed"}


async def test_sync_registry_deactivates_entity_that_lost_the_label(cfg, db, fake_hass):
    both = [entity_entry("input_boolean.a"), entity_entry("input_boolean.b")]
    prepare(fake_hass, both)
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    collector = make_collector(cfg, db, fake_hass)
    try:
        await collector.sync_registry(client)
        set_registry(fake_hass, [entity_entry("input_boolean.a"), entity_entry("input_boolean.b", labels=())])
        await collector.sync_registry(client)
    finally:
        await client.close()
    row = db.conn.execute("SELECT active, tracked_until FROM entities WHERE entity_id='input_boolean.b'").fetchone()
    assert row["active"] == 0 and row["tracked_until"] is not None
    assert set(collector.pipeline.rules) == {"input_boolean.a"}


# --- Lückenabgleich ----------------------------------------------------------

async def test_gap_fill_replays_history_with_gap_source(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    seen: dict = {}

    def history(message):
        seen.update(message)
        return {"input_boolean.someone_in_bed": [
            {"s": "off", "a": {}, "lu": 1767261600.0},
            {"s": "on", "a": {}, "lu": 1767265200.0},
        ]}

    fake_hass.responses[HISTORY] = history
    client = HassClient(fake_hass.url, "secret")
    await client.connect()
    collector = make_collector(cfg, db, fake_hass)
    try:
        await collector.sync_registry(client)
        assert await collector.gap_fill(client) == 2
    finally:
        await client.close()
    rows = db.conn.execute("SELECT state, source FROM state_changes ORDER BY ts_utc").fetchall()
    assert [(r["state"], r["source"]) for r in rows] == [("off", "gap:history_api"), ("on", "gap:history_api")]
    assert seen["entity_ids"] == ["input_boolean.someone_in_bed"]
    assert seen["include_start_time_state"] is True


# --- Sitzung ---------------------------------------------------------------------

async def test_run_session_records_events_and_flushes_on_close(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    fake_hass.events = [
        state_event("input_boolean.someone_in_bed", "on", "2026-01-01T22:00:00+00:00", user="u-fabian"),
        state_event("light.nicht_gelabelt", "on", "2026-01-01T22:00:01+00:00"),
        {"event_type": "state_changed", "data": {"entity_id": "x", "new_state": None}},
    ]
    fake_hass.close_after_command = HISTORY
    client = HassClient(fake_hass.url, "secret")
    collector = make_collector(cfg, db, fake_hass)
    with pytest.raises(HassConnectionError):
        await collector.run_session(client)
    await client.close()
    rows = db.conn.execute("SELECT entity_id, state, context_user_id, source FROM state_changes").fetchall()
    assert [tuple(r) for r in rows] == [("input_boolean.someone_in_bed", "on", "u-fabian", "live")]


async def test_periodic_flush_writes_before_the_session_ends(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    fake_hass.events = [state_event("input_boolean.someone_in_bed", "on", "2026-01-01T22:00:00+00:00")]
    collector = make_collector(replace(cfg, flush_interval_s=0.05), db, fake_hass)
    client = HassClient(fake_hass.url, "secret")
    task = asyncio.create_task(collector.run_session(client))
    count = 0
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            count = db.conn.execute("SELECT COUNT(*) FROM state_changes").fetchone()[0]
            if count:
                break
        assert count == 1
    finally:
        collector.stop()
        await task
        await client.close()


async def test_health_file_is_touched_after_connect(cfg, db, fake_hass, tmp_path):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    fake_hass.close_after_command = HISTORY
    health = tmp_path / "state" / "collector.health"
    collector = make_collector(cfg, db, fake_hass, health_file=health)
    client = HassClient(fake_hass.url, "secret")
    with pytest.raises(HassConnectionError):
        await collector.run_session(client)
    await client.close()
    assert health.exists()


async def test_run_stops_on_stop_request(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    collector = make_collector(cfg, db, fake_hass)
    task = asyncio.create_task(collector.run())
    await asyncio.sleep(0.3)
    collector.stop()
    await asyncio.wait_for(task, 5)


async def test_run_reconnects_after_connection_loss(cfg, db, fake_hass, monkeypatch):
    monkeypatch.setattr(live, "BACKOFF_START", 0.05)
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    fake_hass.close_after_command = HISTORY
    collector = make_collector(cfg, db, fake_hass)
    task = asyncio.create_task(collector.run())
    try:
        subscriptions = 0
        for _ in range(100):
            await asyncio.sleep(0.05)
            subscriptions = len([m for m in fake_hass.received if m["type"] == "subscribe_events"])
            if subscriptions >= 2:
                break
        assert subscriptions >= 2
    finally:
        collector.stop()
        await asyncio.wait_for(task, 5)


async def test_run_does_not_retry_on_auth_error(cfg, db, fake_hass):
    prepare(fake_hass, [entity_entry("input_boolean.someone_in_bed")])
    fake_hass.token = "anderes-token"
    collector = make_collector(cfg, db, fake_hass)
    with pytest.raises(HassAuthError):
        await collector.run()
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_live.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.live'`.

- [ ] **Step 3: Implementieren**

`ai/collector/live.py`:

```python
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
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_live.py -v`
Expected: PASS (15 Tests). Falls `test_run_reconnects_after_connection_loss` hängt, zuerst prüfen, ob `BACKOFF_START` im Test über `monkeypatch` tatsächlich gesetzt wird (der Collector muss die Modulkonstante zur Laufzeit lesen, nicht als Standardargument).

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle bisherigen Tests PASS.

```bash
git add ai/collector/live.py ai/tests/test_live.py
git commit -m "feat(ai): Collector mit Live-Sitzung, Lückenabgleich und Sicherung"
```

### Task 8: Backfill aus einer alten Home-Assistant-Datenbank

**Files:**
- Create: `ai/collector/ids.py`, `ai/collector/backfill.py`, `ai/tests/ha_fixture.py`
- Test: `ai/tests/test_ids.py`, `ai/tests/test_backfill.py`
- Modify: `docs/superpowers/specs/2026-10-13-home-ai-data-foundation-design.md` (Abschnitt 7.2 präzisieren)

**Interfaces:**
- Consumes: `Database` (Aufgabe 3), `ChangeFilter` (Aufgabe 2), `resolve_rule` (Aufgabe 1), `EntityInfo`, `EntityRule`, `Change`, `Config`, `ConfigError`.
- Produces:
  - `collector.ids`: `ulid_to_bytes(text) -> bytes`, `bytes_to_ulid(raw) -> str`, `bytes_to_hex(raw) -> str`
  - `collector.backfill`:
    - `class UnsupportedSchema(Exception)`, `class CorruptDatabase(Exception)`
    - `@dataclass class BackfillResult(rows_read, rows_inserted, first_ts, last_ts)`
    - `@dataclass(frozen=True) class HistoryMapEntry(entity_id, room, role="climate", domain="heating", kind="climate", valid_from=None, valid_to=None)`
    - `load_history_map(path) -> list[HistoryMapEntry]`
    - `history_rules(entries, cfg, labeled_ids: set[str]) -> tuple[dict[str, EntityRule], dict[str, str]]` (Regeln und Räume nur für Einträge ohne Label)
    - `backfill_ha_db(ha_db_path, db, rules, rooms, *, history_only=frozenset(), since=None, until=None, now=None, check_integrity=True) -> BackfillResult`
    - `backfill_with_map(cfg, db, infos: list[EntityInfo], entries: list[HistoryMapEntry], ha_db_path, *, since=None, until=None, check_integrity=True) -> BackfillResult`
  - Test-Hilfe `tests.ha_fixture.build_ha_db(path, rows, events=None, schema_version=48) -> Path` mit Zeilen `{"entity_id", "state", "ts", "attrs", "ctx", "user", "parent"}` und Ereignissen `{"ts", "data"}`.

- [ ] **Step 1: Tests für die ID-Umwandlung schreiben**

`ai/tests/test_ids.py`:

```python
import pytest

from collector.ids import bytes_to_hex, bytes_to_ulid, ulid_to_bytes


@pytest.mark.parametrize("ulid", ["01M4TXPBEYE60APXTRFD3PKB80", "01M4TXPBESW8FDM38QJVTXJZS9"])
def test_ulid_roundtrip(ulid):
    raw = ulid_to_bytes(ulid)
    assert len(raw) == 16
    assert bytes_to_ulid(raw) == ulid


def test_hex_user_id_roundtrip():
    user_id = "45171d305dd5422ca9d0ecf755abbda7"
    assert bytes_to_hex(bytes.fromhex(user_id)) == user_id
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_ids.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.ids'`.

- [ ] **Step 3: ID-Umwandlung implementieren**

`ai/collector/ids.py`:

```python
from __future__ import annotations

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford-Base32 wie in ULIDs


def ulid_to_bytes(value: str) -> bytes:
    number = 0
    for char in value.upper():
        number = number * 32 + ALPHABET.index(char)
    return number.to_bytes(16, "big")


def bytes_to_ulid(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    chars = []
    for _ in range(26):
        chars.append(ALPHABET[number & 31])
        number >>= 5
    return "".join(reversed(chars))


def bytes_to_hex(raw: bytes) -> str:
    return raw.hex()
```

- [ ] **Step 4: Test laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_ids.py -v`
Expected: PASS (3 Tests).

- [ ] **Step 5: Test-Hilfe für eine kleine Home-Assistant-Datenbank schreiben**

`ai/tests/ha_fixture.py`:

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from collector.ids import ulid_to_bytes

CTX = "01M4TXPBEYE60APXTRFD3PKB80"
PARENT = "01M4TXPBESW8FDM38QJVTXJZS9"
USER_FABIAN = "45171d305dd5422ca9d0ecf755abbda7"
USER_SVENJA = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"


def build_ha_db(path: Path, rows: list[dict], events: list[dict] | None = None,
                schema_version: int = 48) -> Path:
    """Baut eine minimale home-assistant_v2.db im aktuellen Schema (Zeitstempel und *_bin-Spalten)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_changes(change_id INTEGER PRIMARY KEY, schema_version INTEGER, changed TEXT);
        CREATE TABLE states_meta(metadata_id INTEGER PRIMARY KEY, entity_id TEXT);
        CREATE TABLE state_attributes(attributes_id INTEGER PRIMARY KEY, shared_attrs TEXT);
        CREATE TABLE states(
          state_id INTEGER PRIMARY KEY, metadata_id INTEGER, state TEXT, attributes_id INTEGER,
          last_updated_ts REAL, context_id_bin BLOB, context_user_id_bin BLOB, context_parent_id_bin BLOB);
        CREATE TABLE event_types(event_type_id INTEGER PRIMARY KEY, event_type TEXT);
        CREATE TABLE event_data(data_id INTEGER PRIMARY KEY, shared_data TEXT);
        CREATE TABLE events(event_id INTEGER PRIMARY KEY, event_type_id INTEGER, data_id INTEGER, time_fired_ts REAL);
        """
    )
    conn.execute("INSERT INTO schema_changes(schema_version, changed) VALUES (?, 'now')", (schema_version,))
    meta: dict[str, int] = {}
    for row in rows:
        entity_id = row["entity_id"]
        if entity_id not in meta:
            meta[entity_id] = conn.execute(
                "INSERT INTO states_meta(entity_id) VALUES (?)", (entity_id,)
            ).lastrowid
        attributes_id = None
        if row.get("attrs") is not None:
            attributes_id = conn.execute(
                "INSERT INTO state_attributes(shared_attrs) VALUES (?)", (json.dumps(row["attrs"]),)
            ).lastrowid
        conn.execute(
            "INSERT INTO states(metadata_id, state, attributes_id, last_updated_ts, context_id_bin, "
            "context_user_id_bin, context_parent_id_bin) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                meta[entity_id], row["state"], attributes_id, row["ts"],
                ulid_to_bytes(row["ctx"]) if row.get("ctx") else None,
                bytes.fromhex(row["user"]) if row.get("user") else None,
                ulid_to_bytes(row["parent"]) if row.get("parent") else None,
            ),
        )
    if events:
        type_id = conn.execute(
            "INSERT INTO event_types(event_type) VALUES ('thermostat_actor_logged')"
        ).lastrowid
        for event in events:
            data_id = conn.execute(
                "INSERT INTO event_data(shared_data) VALUES (?)", (json.dumps(event["data"]),)
            ).lastrowid
            conn.execute(
                "INSERT INTO events(event_type_id, data_id, time_fired_ts) VALUES (?, ?, ?)",
                (type_id, data_id, event["ts"]),
            )
    conn.commit()
    conn.close()
    return path
```

- [ ] **Step 6: Tests für den Backfill schreiben**

`ai/tests/test_backfill.py`:

```python
import sqlite3
from datetime import datetime, timezone

import pytest

from collector.backfill import (
    BackfillResult,
    UnsupportedSchema,
    HistoryMapEntry,
    backfill_ha_db,
    backfill_with_map,
    history_rules,
    load_history_map,
)
from collector.config import ConfigError
from collector.models import EntityInfo
from collector.rules import resolve_rule
from tests.ha_fixture import CTX, PARENT, USER_FABIAN, build_ha_db

CLIMATE_ATTRS = {"temperature": 17, "preset_mode": "none", "hvac_action": "idle",
                 "current_temperature": 20.0, "window_open": False, "heating_power": 0.02}


def row(entity_id, state, ts, attrs=None, **extra):
    return {"entity_id": entity_id, "state": state, "ts": ts, "attrs": attrs, **extra}


def rules_for(cfg, *entity_ids):
    return {e: resolve_rule(EntityInfo(e), cfg) for e in entity_ids}


def test_backfill_reads_states_converts_contexts_and_applies_filter(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("climate.bt", "heat", 1767261600.0, CLIMATE_ATTRS),
        row("climate.bt", "heat", 1767261660.0, CLIMATE_ATTRS),                         # unverändert -> verworfen
        row("climate.bt", "heat", 1767261720.0, {**CLIMATE_ATTRS, "temperature": 21},
            ctx=CTX, user=USER_FABIAN),
        row("climate.bt", "heat", 1767261780.0, {**CLIMATE_ATTRS, "temperature": 21, "preset_mode": "comfort"},
            ctx=CTX, parent=PARENT),
        row("sensor.not_selected", "5", 1767261600.0),
    ])
    result = backfill_ha_db(ha, db, rules_for(cfg, "climate.bt"), {"climate.bt": "buero"})
    assert isinstance(result, BackfillResult)
    assert (result.rows_read, result.rows_inserted) == (4, 3)
    rows = db.conn.execute(
        "SELECT state, attrs, context_id, context_user_id, context_parent_id, source "
        "FROM state_changes ORDER BY ts_utc").fetchall()
    assert len(rows) == 3
    assert rows[1]["context_id"] == CTX and rows[1]["context_user_id"] == USER_FABIAN
    assert rows[2]["context_parent_id"] == PARENT and rows[2]["context_user_id"] is None
    assert rows[0]["source"] == "backfill:home-assistant_v2.db"
    assert "heating_power" not in rows[0]["attrs"]
    assert db.conn.execute("SELECT room FROM entities WHERE entity_id='climate.bt'").fetchone()["room"] == "buero"
    run = db.conn.execute("SELECT source, rows, status FROM ingest_runs").fetchone()
    assert (run["source"], run["rows"], run["status"]) == ("backfill:home-assistant_v2.db", 3, "ok")


def test_backfill_skips_null_states_and_is_idempotent(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("input_boolean.x", "off", 1767261600.0),
        row("input_boolean.x", None, 1767261601.0),
        row("input_boolean.x", "on", 1767261700.0),
        row("input_boolean.empty_window", "on", 1900000000.0),
    ])
    rules = rules_for(cfg, "input_boolean.x", "input_boolean.never_seen")
    first = backfill_ha_db(ha, db, rules, {})
    second = backfill_ha_db(ha, db, rules, {})
    assert (first.rows_read, first.rows_inserted) == (3 - 1, 2)
    assert second.rows_inserted == 0
    assert db.count_changes("input_boolean.x") == 2
    assert db.count_changes("input_boolean.never_seen") == 0


def test_backfill_respects_since_and_until(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("input_boolean.x", "off", 1000.0),
        row("input_boolean.x", "on", 2000.0),
        row("input_boolean.x", "off", 3000.0),
    ])
    since = datetime.fromtimestamp(1500.0, tz=timezone.utc)
    until = datetime.fromtimestamp(2500.0, tz=timezone.utc)
    result = backfill_ha_db(ha, db, rules_for(cfg, "input_boolean.x"), {}, since=since, until=until)
    assert result.rows_read == 1
    assert [r["state"] for r in db.conn.execute("SELECT state FROM state_changes")] == ["on"]


def test_unsupported_schema_aborts_before_writing(cfg, db, tmp_path):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript("CREATE TABLE states(state_id INTEGER, entity_id TEXT, state TEXT);")
    conn.commit()
    conn.close()
    with pytest.raises(UnsupportedSchema, match="Schema"):
        backfill_ha_db(old, db, rules_for(cfg, "input_boolean.x"), {})
    assert db.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM ingest_runs").fetchone()[0] == 0


def test_history_only_entities_are_marked_inactive_with_last_timestamp(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("climate.trv_office", "heat", 1767261600.0, CLIMATE_ATTRS),
        row("climate.trv_office", "heat", 1767265200.0, {**CLIMATE_ATTRS, "temperature": 20}),
    ])
    rules = rules_for(cfg, "climate.trv_office")
    backfill_ha_db(ha, db, rules, {"climate.trv_office": "buero"}, history_only={"climate.trv_office"})
    entity = db.conn.execute("SELECT active, tracked_until, tracked_since FROM entities").fetchone()
    assert entity["active"] == 0
    assert entity["tracked_until"] == "2026-01-01T11:00:00.000000+00:00"
    assert entity["tracked_since"] == "2026-01-01T10:00:00.000000+00:00"


def test_backfilled_user_change_shows_up_in_manual_actions(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("climate.bt", "heat", 1767261600.0, CLIMATE_ATTRS),
        row("climate.bt", "heat", 1767261720.0, {**CLIMATE_ATTRS, "temperature": 21}, ctx=CTX, user=USER_FABIAN),
    ])
    backfill_ha_db(ha, db, rules_for(cfg, "climate.bt"), {"climate.bt": "buero"})
    (action,) = db.conn.execute(
        "SELECT actor_class, actor_user_id, changed_attrs FROM manual_actions").fetchall()
    assert (action["actor_class"], action["actor_user_id"], action["changed_attrs"]) == (
        "manual", USER_FABIAN, "temperature")


def test_history_map_loads_entries_and_rejects_unknown_keys(tmp_path):
    good = tmp_path / "h.yaml"
    good.write_text(
        "entities:\n"
        "  - {entity_id: climate.trv_office, room: buero, valid_to: '2025-12-01T00:00:00+00:00'}\n"
        "  - {entity_id: climate.trv_bathroom, room: bad, role: thermostat_trv}\n",
        encoding="utf-8")
    entries = load_history_map(good)
    assert entries[0] == HistoryMapEntry("climate.trv_office", "buero", valid_to="2025-12-01T00:00:00+00:00")
    assert entries[1].role == "thermostat_trv"
    bad = tmp_path / "b.yaml"
    bad.write_text("entities:\n  - {entity_id: climate.x, room: r, nope: 1}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="nope"):
        load_history_map(bad)


def test_history_rules_skip_labeled_entities(cfg):
    entries = [HistoryMapEntry("climate.trv_office", "buero"), HistoryMapEntry("climate.bt", "buero")]
    rules, rooms = history_rules(entries, cfg, labeled_ids={"climate.bt"})
    assert set(rules) == {"climate.trv_office"}
    assert rooms == {"climate.trv_office": "buero"}
    assert (rules["climate.trv_office"].kind, rules["climate.trv_office"].domain) == ("climate", "heating")


def test_backfill_with_map_combines_labeled_and_history_entities(cfg, db, tmp_path):
    ha = build_ha_db(tmp_path / "home-assistant_v2.db", [
        row("climate.bt", "heat", 1767261600.0, CLIMATE_ATTRS),
        row("climate.trv_office", "heat", 1767261600.0, CLIMATE_ATTRS),
    ])
    entries = [HistoryMapEntry("climate.trv_office", "buero", valid_to="2026-01-02T00:00:00+00:00")]
    result = backfill_with_map(cfg, db, [EntityInfo("climate.bt", None, "buero")], entries, ha)
    assert result.rows_inserted == 2
    states = {r["entity_id"]: r["active"] for r in db.conn.execute("SELECT entity_id, active FROM entities")}
    assert states == {"climate.bt": 1, "climate.trv_office": 0}
    room = db.conn.execute("SELECT room, valid_to FROM room_map WHERE entity_id='climate.trv_office'").fetchone()
    assert (room["room"], room["valid_to"]) == ("buero", "2026-01-02T00:00:00+00:00")
```

- [ ] **Step 7: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_backfill.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.backfill'`.

- [ ] **Step 8: Backfill implementieren**

`ai/collector/backfill.py`:

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

from .config import Config, ConfigError
from .db import Database
from .filtering import ChangeFilter
from .ids import bytes_to_hex, bytes_to_ulid
from .models import Change, EntityInfo, EntityRule, to_utc_iso, utcnow
from .rules import resolve_rule

CHUNK = 500
BATCH = 5000
REQUIRED_STATE_COLUMNS = {
    "state_id", "metadata_id", "state", "attributes_id", "last_updated_ts",
    "context_id_bin", "context_user_id_bin", "context_parent_id_bin",
}
REQUIRED_TABLES = {"states", "states_meta", "state_attributes"}
HISTORY_KEYS = {"entity_id", "room", "role", "domain", "kind", "valid_from", "valid_to"}


class UnsupportedSchema(Exception):
    pass


class CorruptDatabase(Exception):
    pass


@dataclass
class BackfillResult:
    rows_read: int
    rows_inserted: int
    first_ts: str | None
    last_ts: str | None


@dataclass(frozen=True)
class HistoryMapEntry:
    entity_id: str
    room: str
    role: str = "climate"
    domain: str = "heating"
    kind: str = "climate"
    valid_from: str | None = None
    valid_to: str | None = None


def load_history_map(path: str | Path) -> list[HistoryMapEntry]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = []
    for item in raw.get("entities", []):
        unknown = set(item) - HISTORY_KEYS
        if unknown:
            raise ConfigError(f"history_map: unbekannte Schlüssel {sorted(unknown)} bei {item.get('entity_id')}")
        entries.append(HistoryMapEntry(**item))
    return entries


def history_rules(entries: Iterable[HistoryMapEntry], cfg: Config,
                  labeled_ids: set[str]) -> tuple[dict[str, EntityRule], dict[str, str]]:
    rules: dict[str, EntityRule] = {}
    rooms: dict[str, str] = {}
    for entry in entries:
        if entry.entity_id in labeled_ids or entry.entity_id in rules:
            continue
        info = EntityInfo(entry.entity_id, None, entry.room)
        rules[entry.entity_id] = resolve_rule(info, cfg, override={"kind": entry.kind, "domain": entry.domain})
        rooms[entry.entity_id] = entry.room
    return rules, rooms


def open_readonly(path: str | Path) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def check_ha_schema(conn: sqlite3.Connection) -> None:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    columns = {row[1] for row in conn.execute("PRAGMA table_info(states)")}
    missing_tables = REQUIRED_TABLES - tables
    missing_columns = REQUIRED_STATE_COLUMNS - columns
    if missing_tables or missing_columns:
        version = None
        if "schema_changes" in tables:
            found = conn.execute(
                "SELECT schema_version FROM schema_changes ORDER BY change_id DESC LIMIT 1").fetchone()
            version = found[0] if found else None
        raise UnsupportedSchema(
            f"Unbekanntes Schema der Home-Assistant-Datenbank (Schemaversion {version}). "
            f"Es fehlen Tabellen {sorted(missing_tables)} und Spalten {sorted(missing_columns)}. "
            "Unterstützt wird das aktuelle Schema mit Zeitstempel- und *_bin-Spalten."
        )


def verify_integrity(conn: sqlite3.Connection) -> None:
    answer = conn.execute("PRAGMA quick_check(1)").fetchone()[0]
    if answer != "ok":
        raise CorruptDatabase(f"Die Datenbank ist beschädigt (quick_check: {answer}). "
                              "-wal und -shm mitkopieren oder einen anderen Snapshot nehmen.")


def _query_rows(conn: sqlite3.Connection, entity_ids: list[str],
                since_ts: float | None, until_ts: float | None) -> Iterable[sqlite3.Row]:
    placeholders = ",".join("?" for _ in entity_ids)
    params: list = list(entity_ids)
    time_filter = ""
    if since_ts is not None:
        time_filter += " AND s.last_updated_ts >= ?"
        params.append(since_ts)
    if until_ts is not None:
        time_filter += " AND s.last_updated_ts < ?"
        params.append(until_ts)
    sql = f"""
        SELECT sm.entity_id AS entity_id, s.state AS state, s.last_updated_ts AS ts,
               sa.shared_attrs AS attrs, s.context_id_bin AS ctx,
               s.context_user_id_bin AS usr, s.context_parent_id_bin AS parent
        FROM states s
        JOIN states_meta sm ON sm.metadata_id = s.metadata_id
        LEFT JOIN state_attributes sa ON sa.attributes_id = s.attributes_id
        WHERE sm.entity_id IN ({placeholders}){time_filter}
        ORDER BY s.last_updated_ts, s.state_id
    """
    return conn.execute(sql, params)


def _row_to_change(row: sqlite3.Row) -> Change:
    try:
        attrs = json.loads(row["attrs"]) if row["attrs"] else {}
    except ValueError:
        attrs = {}
    return Change(
        ts=datetime.fromtimestamp(row["ts"], tz=timezone.utc),
        entity_id=row["entity_id"],
        state=row["state"],
        attrs=attrs,
        context_id=bytes_to_ulid(row["ctx"]) if row["ctx"] else None,
        context_user_id=bytes_to_hex(row["usr"]) if row["usr"] else None,
        context_parent_id=bytes_to_ulid(row["parent"]) if row["parent"] else None,
    )


def backfill_ha_db(ha_db_path: str | Path, db: Database, rules: dict[str, EntityRule],
                   rooms: dict[str, str | None], *, history_only: frozenset[str] | set[str] = frozenset(),
                   since: datetime | None = None, until: datetime | None = None,
                   now: datetime | None = None, check_integrity: bool = True) -> BackfillResult:
    started = now or utcnow()
    source = f"backfill:{Path(ha_db_path).name}"
    src = open_readonly(ha_db_path)
    try:
        check_ha_schema(src)
        if check_integrity:
            verify_integrity(src)
        for entity_id, rule in rules.items():
            db.upsert_entity(rule, rooms.get(entity_id), started)
        filters = {entity_id: ChangeFilter(rule) for entity_id, rule in rules.items()}
        since_ts = since.timestamp() if since else None
        until_ts = until.timestamp() if until else None
        rows_read = inserted = 0
        first_ts = last_ts = None
        last_by_entity: dict[str, datetime] = {}
        batch: list[Change] = []
        entity_ids = sorted(rules)
        try:
            for start in range(0, len(entity_ids), CHUNK):
                for row in _query_rows(src, entity_ids[start:start + CHUNK], since_ts, until_ts):
                    if row["state"] is None or row["ts"] is None:
                        continue
                    rows_read += 1
                    change = _row_to_change(row)
                    last_by_entity[change.entity_id] = change.ts
                    stamp = to_utc_iso(change.ts)
                    first_ts = stamp if first_ts is None or stamp < first_ts else first_ts
                    last_ts = stamp if last_ts is None or stamp > last_ts else last_ts
                    batch.extend(filters[change.entity_id].feed(change))
                    if len(batch) >= BATCH:
                        inserted += db.insert_changes(batch, source, commit=False)
                        batch = []
            for change_filter in filters.values():
                batch.extend(change_filter.flush_pending())
            inserted += db.insert_changes(batch, source, commit=False)
            db.commit()
        except Exception:
            db.rollback()
            raise
        for entity_id in history_only:
            db.mark_inactive(entity_id, last_by_entity.get(entity_id) or started)
        db.record_run(source, first_ts, last_ts, inserted, "ok", to_utc_iso(started))
        return BackfillResult(rows_read, inserted, first_ts, last_ts)
    finally:
        src.close()


def backfill_with_map(cfg: Config, db: Database, infos: list[EntityInfo],
                      entries: list[HistoryMapEntry], ha_db_path: str | Path, *,
                      since: datetime | None = None, until: datetime | None = None,
                      check_integrity: bool = True) -> BackfillResult:
    """Gelabelte Entitäten plus Einträge der history_map (nur Vergangenes)."""
    rules = {info.entity_id: resolve_rule(info, cfg) for info in infos}
    rooms: dict[str, str | None] = {info.entity_id: info.area for info in infos}
    extra_rules, extra_rooms = history_rules(entries, cfg, set(rules))
    for entry in entries:
        db.add_history_room(entry.room, entry.entity_id, entry.role, entry.valid_from, entry.valid_to)
    rules.update(extra_rules)
    rooms.update(extra_rooms)
    return backfill_ha_db(ha_db_path, db, rules, rooms, history_only=set(extra_rules),
                          since=since, until=until, check_integrity=check_integrity)
```

- [ ] **Step 9: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_backfill.py tests/test_ids.py -v`
Expected: PASS (12 Tests).

- [ ] **Step 10: Spezifikation präzisieren**

In `docs/superpowers/specs/2026-10-13-home-ai-data-foundation-design.md` (Abschnitt 7.2) ersetzen:

```
- **Welche Entitäten** eingespielt werden, bestimmt dasselbe Label wie beim Collector (Entitäten mit `KI_DB_Sync`).
```

durch:

```
- **Welche Entitäten** eingespielt werden: alle mit dem Label `KI_DB_Sync` (wie beim Collector) plus die Einträge der `history_map.yaml`. Letztere betreffen nur Vergangenes (z. B. frühere `climate.trv_*`), werden als inaktiv geführt (`active = 0`, `tracked_until` = letzter importierter Zeitstempel) und beeinflussen die Live-Aufzeichnung nicht.
```

- [ ] **Step 11: Commit**

```bash
git add ai/collector/ids.py ai/collector/backfill.py ai/tests/ha_fixture.py ai/tests/test_ids.py ai/tests/test_backfill.py docs/superpowers/specs/2026-10-13-home-ai-data-foundation-design.md
git commit -m "feat(ai): Backfill aus alter Home-Assistant-Datenbank mit history_map"
```

---

### Task 9: Abgleich mit den protokollierten `thermostat_actor_logged`-Ereignissen

**Files:**
- Create: `ai/collector/verify.py`
- Test: `ai/tests/test_verify.py`

**Interfaces:**
- Consumes: `Database` (View `manual_actions`), `open_readonly` (Aufgabe 8), Test-Hilfe `build_ha_db`, `backfill_ha_db`.
- Produces: `@dataclass class VerifyReport(logged_manual, matched, untracked_entity, unmatched: list[dict], extra_manual)` mit Property `match_rate: float` (Anteil `matched / (logged_manual - untracked_entity)`, 1.0 wenn nichts zu prüfen ist) und `verify_logged_events(ha_db_path, db, tolerance_s: float = 5.0) -> VerifyReport`. Ereignisse mit `actor_id` leer oder `"unknown"` zählen nicht als manuell.

- [ ] **Step 1: Test schreiben**

`ai/tests/test_verify.py`:

```python
import pytest

from collector.backfill import backfill_ha_db
from collector.models import EntityInfo
from collector.rules import resolve_rule
from collector.verify import verify_logged_events
from tests.ha_fixture import CTX, USER_FABIAN, USER_SVENJA, build_ha_db

ATTRS = {"temperature": 17, "preset_mode": "none", "hvac_action": "idle",
         "current_temperature": 20.0, "window_open": False}


def climate_row(ts, state="heat", temperature=17, **extra):
    return {"entity_id": "climate.bt", "state": state, "ts": ts,
            "attrs": {**ATTRS, "temperature": temperature}, **extra}


def logged(ts, entity_id, change_types, actor_id):
    return {"ts": ts, "data": {"entity_id": entity_id, "change_types": change_types, "actor_id": actor_id}}


@pytest.fixture
def prepared(cfg, db, tmp_path):
    ha = build_ha_db(
        tmp_path / "home-assistant_v2.db",
        [
            climate_row(1000.0),
            climate_row(2000.0, temperature=21, ctx=CTX, user=USER_FABIAN),
            climate_row(3000.0, state="off", temperature=21, ctx=CTX, user=USER_SVENJA),
        ],
        events=[
            logged(2000.5, "climate.bt", ["setpoint"], USER_FABIAN),     # passt
            logged(3000.2, "climate.bt", ["hvac_mode"], USER_SVENJA),    # passt
            logged(4000.0, "climate.bt", ["setpoint"], USER_FABIAN),     # keine passende Zeile
            logged(5000.0, "climate.other", ["setpoint"], USER_FABIAN),  # Entität nicht aufgezeichnet
            logged(6000.0, "climate.bt", ["setpoint"], "unknown"),       # nicht manuell
        ],
    )
    rules = {"climate.bt": resolve_rule(EntityInfo("climate.bt"), cfg)}
    backfill_ha_db(ha, db, rules, {"climate.bt": "buero"})
    return ha


def test_report_counts_matches_untracked_and_unmatched(db, prepared):
    report = verify_logged_events(prepared, db)
    assert report.logged_manual == 4
    assert report.matched == 2
    assert report.untracked_entity == 1
    assert [item["entity_id"] for item in report.unmatched] == ["climate.bt"]
    assert report.extra_manual == 0
    assert report.match_rate == pytest.approx(2 / 3)


def test_wrong_actor_does_not_match(db, prepared, tmp_path, cfg):
    other = build_ha_db(
        tmp_path / "other.db",
        [climate_row(1000.0), climate_row(2000.0, temperature=21, ctx=CTX, user=USER_FABIAN)],
        events=[logged(2000.5, "climate.bt", ["setpoint"], USER_SVENJA)],
    )
    report = verify_logged_events(other, db)
    assert (report.logged_manual, report.matched) == (1, 0)


def test_tolerance_is_respected(db, prepared, tmp_path):
    late = build_ha_db(
        tmp_path / "late.db",
        [climate_row(1000.0), climate_row(2000.0, temperature=21, ctx=CTX, user=USER_FABIAN)],
        events=[logged(2060.0, "climate.bt", ["setpoint"], USER_FABIAN)],
    )
    assert verify_logged_events(late, db, tolerance_s=5).matched == 0
    assert verify_logged_events(late, db, tolerance_s=120).matched == 1


def test_extra_manual_actions_are_counted(db, prepared, tmp_path):
    none_logged = build_ha_db(tmp_path / "none.db", [climate_row(1000.0)], events=[])
    report = verify_logged_events(none_logged, db)
    assert report.logged_manual == 0
    assert report.extra_manual == 2
    assert report.match_rate == 1.0
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_verify.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.verify'`.

- [ ] **Step 3: Implementieren**

`ai/collector/verify.py`:

```python
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .backfill import open_readonly
from .db import Database
from .models import parse_utc

EVENTS_SQL = """
SELECT e.time_fired_ts AS ts, ed.shared_data AS data
FROM events e
JOIN event_types et ON et.event_type_id = e.event_type_id
JOIN event_data ed ON ed.data_id = e.data_id
WHERE et.event_type = 'thermostat_actor_logged'
ORDER BY e.time_fired_ts
"""


@dataclass
class VerifyReport:
    logged_manual: int = 0
    matched: int = 0
    untracked_entity: int = 0
    unmatched: list[dict] = field(default_factory=list)
    extra_manual: int = 0

    @property
    def match_rate(self) -> float:
        considered = self.logged_manual - self.untracked_entity
        return 1.0 if considered <= 0 else self.matched / considered


def verify_logged_events(ha_db_path: str | Path, db: Database, tolerance_s: float = 5.0) -> VerifyReport:
    src = open_readonly(ha_db_path)
    try:
        events = src.execute(EVENTS_SQL).fetchall()
    finally:
        src.close()
    tracked = {row["entity_id"] for row in db.conn.execute("SELECT entity_id FROM entities")}
    manual = db.conn.execute(
        "SELECT ts_utc, entity_id, changed_attrs, actor_user_id FROM manual_actions "
        "WHERE actor_class = 'manual' ORDER BY ts_utc"
    ).fetchall()
    by_entity: dict[str, list[tuple[int, datetime, set[str], str | None]]] = defaultdict(list)
    for index, row in enumerate(manual):
        by_entity[row["entity_id"]].append(
            (index, parse_utc(row["ts_utc"]), set(row["changed_attrs"].split(",")), row["actor_user_id"])
        )

    report = VerifyReport()
    used: set[int] = set()
    for event in events:
        data = json.loads(event["data"])
        actor = data.get("actor_id")
        if not actor or actor == "unknown":
            continue
        report.logged_manual += 1
        entity_id = data["entity_id"]
        if entity_id not in tracked:
            report.untracked_entity += 1
            continue
        wanted = set()
        change_types = data.get("change_types") or []
        if "setpoint" in change_types:
            wanted.add("temperature")
        if "hvac_mode" in change_types:
            wanted.add("hvac_mode")
        fired = datetime.fromtimestamp(event["ts"], tz=timezone.utc)
        hit = next(
            (index for index, ts, changed, user in by_entity[entity_id]
             if index not in used and user == actor and changed & wanted
             and abs((ts - fired).total_seconds()) <= tolerance_s),
            None,
        )
        if hit is None:
            report.unmatched.append({"entity_id": entity_id, "ts": fired.isoformat(),
                                     "change_types": change_types, "actor_id": actor})
        else:
            used.add(hit)
            report.matched += 1
    report.extra_manual = len(manual) - len(used)
    return report
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_verify.py -v`
Expected: PASS (4 Tests).

- [ ] **Step 5: Commit**

```bash
git add ai/collector/verify.py ai/tests/test_verify.py
git commit -m "feat(ai): Abgleich der manuellen Eingriffe mit thermostat_actor_logged"
```

---

### Task 10: Kommandozeile und Healthcheck

**Files:**
- Create: `ai/collector/cli.py`, `ai/collector/__main__.py`, `ai/collector/healthcheck.py`
- Test: `ai/tests/test_cli.py`, `ai/tests/test_healthcheck.py`

**Interfaces:**
- Consumes: `load_config`, `Collector`, `HassClient`, `Database`, `fetch_labeled_entities`, `backfill_with_map`, `load_history_map`, `verify_logged_events`, alle Fehlerklassen.
- Produces:
  - `collector.cli.main(argv: list[str] | None = None) -> int` mit Unterbefehlen `run`, `backfill --db <Datei> [--from TS] [--to TS] [--history-map DATEI] [--skip-integrity-check]`, `verify --db <Datei> [--min-match-rate 0.95]`, `purge-entity ENTITY_ID [--yes]`; globale Option `--config` (Standard: `COLLECTOR_CONFIG` oder `/config/collector.yaml`). Rückgabewert 0 = Erfolg, 1 = Prüfung nicht bestanden oder abgebrochen, 2 = Konfigurations- oder Verbindungsfehler (Meldung auf stderr).
  - `collector.healthcheck.main() -> int` (0, wenn die Datei aus `HEALTH_FILE` höchstens 600 s alt ist).

- [ ] **Step 1: Tests schreiben**

`ai/tests/test_cli.py`:

```python
import pytest

from collector.cli import main
from collector.db import Database
from collector.models import Change, EntityRule
from tests.conftest import ROOT

CONFIG = str(ROOT / "config" / "collector.yaml")


@pytest.fixture
def env(monkeypatch, tmp_path):
    db_path = tmp_path / "home_ai.sqlite"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("HA_URL", "ws://ha.test/api/websocket")
    monkeypatch.delenv("HA_TOKEN", raising=False)
    database = Database(db_path)
    from datetime import datetime, timezone
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    database.upsert_entity(EntityRule("climate.bt", "climate", "heating"), "buero", now)
    database.insert_changes([Change(now, "climate.bt", "heat"), Change(now.replace(hour=1), "climate.bt", "off")], "live")
    database.close()
    return db_path


def count(db_path) -> int:
    database = Database(db_path)
    try:
        return database.count_changes("climate.bt")
    finally:
        database.close()


def test_purge_entity_aborts_without_confirmation(env, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt: "nein")
    assert main(["--config", CONFIG, "purge-entity", "climate.bt"]) == 1
    assert "2 Zeilen" in capsys.readouterr().out
    assert count(env) == 2


def test_purge_entity_deletes_after_confirmation(env, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "ja")
    assert main(["--config", CONFIG, "purge-entity", "climate.bt"]) == 0
    assert count(env) == 0


def test_purge_entity_with_yes_flag_skips_prompt(env, monkeypatch):
    def fail(_prompt):
        raise AssertionError("keine Rückfrage erwartet")

    monkeypatch.setattr("builtins.input", fail)
    assert main(["--config", CONFIG, "purge-entity", "climate.bt", "--yes"]) == 0
    assert count(env) == 0


def test_run_without_token_reports_error(env, capsys):
    assert main(["--config", CONFIG, "run"]) == 2
    assert "HA_TOKEN" in capsys.readouterr().err


def test_missing_config_file_reports_error(env, capsys):
    assert main(["--config", "/gibt/es/nicht.yaml", "purge-entity", "climate.bt"]) == 2
    assert "nicht.yaml" in capsys.readouterr().err
```

`ai/tests/test_healthcheck.py`:

```python
import os
import time

from collector.healthcheck import main


def test_fresh_file_is_healthy(tmp_path, monkeypatch):
    health = tmp_path / "h"
    health.touch()
    monkeypatch.setenv("HEALTH_FILE", str(health))
    assert main() == 0


def test_old_file_is_unhealthy(tmp_path, monkeypatch, capsys):
    health = tmp_path / "h"
    health.touch()
    old = time.time() - 700
    os.utime(health, (old, old))
    monkeypatch.setenv("HEALTH_FILE", str(health))
    assert main() == 1
    assert "älter" in capsys.readouterr().out


def test_missing_file_is_unhealthy(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_FILE", str(tmp_path / "gibt-es-nicht"))
    assert main() == 1
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_cli.py tests/test_healthcheck.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'collector.cli'`.

- [ ] **Step 3: Healthcheck und Einstiegspunkt implementieren**

`ai/collector/healthcheck.py`:

```python
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

MAX_AGE_S = 600


def main() -> int:
    path = Path(os.environ.get("HEALTH_FILE", "/tmp/collector.health"))
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        print(f"Keine Health-Datei {path}")
        return 1
    if age > MAX_AGE_S:
        print(f"Letzte Nachricht von Home Assistant ist älter als {MAX_AGE_S} s ({age:.0f} s)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`ai/collector/__main__.py`:

```python
from .cli import main

raise SystemExit(main())
```

- [ ] **Step 4: Kommandozeile implementieren**

`ai/collector/cli.py`:

```python
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from .backfill import CorruptDatabase, UnsupportedSchema, backfill_with_map, load_history_map
from .config import Config, ConfigError, load_config
from .db import Database
from .hass_ws import HassAuthError, HassClient, HassConnectionError
from .live import Collector
from .models import parse_utc
from .registry import LabelEmpty, LabelNotFound, fetch_labeled_entities
from .verify import verify_logged_events


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collector", description="KI-Datenbasis für Home Assistant")
    parser.add_argument("--config", default=os.environ.get("COLLECTOR_CONFIG", "/config/collector.yaml"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Collector starten")
    backfill = sub.add_parser("backfill", help="alte home-assistant_v2.db einspielen (nur lesend)")
    backfill.add_argument("--db", required=True)
    backfill.add_argument("--from", dest="since")
    backfill.add_argument("--to", dest="until")
    backfill.add_argument("--history-map")
    backfill.add_argument("--skip-integrity-check", action="store_true")
    verify = sub.add_parser("verify", help="manual_actions mit thermostat_actor_logged vergleichen")
    verify.add_argument("--db", required=True)
    verify.add_argument("--min-match-rate", type=float, default=0.95)
    purge = sub.add_parser("purge-entity", help="alle Daten einer Entität endgültig löschen")
    purge.add_argument("entity_id")
    purge.add_argument("--yes", action="store_true")
    return parser


def _token() -> str:
    token = os.environ.get("HA_TOKEN")
    if not token:
        raise ConfigError("Umgebungsvariable HA_TOKEN fehlt (Long-Lived Access Token von Home Assistant)")
    return token


async def _run(cfg: Config) -> None:
    token = _token()
    db = Database(cfg.database)
    collector = Collector(
        cfg, db, lambda: HassClient(cfg.ha_url, token),
        health_file=Path(os.environ.get("HEALTH_FILE", "/tmp/collector.health")),
    )
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, collector.stop)
        except NotImplementedError:      # Windows
            pass
    try:
        await collector.run()
    finally:
        db.close()


async def _labeled(cfg: Config):
    client = HassClient(cfg.ha_url, _token())
    await client.connect()
    try:
        return await fetch_labeled_entities(client, cfg.label)
    finally:
        await client.close()


def _cmd_backfill(cfg: Config, args: argparse.Namespace) -> int:
    infos = asyncio.run(_labeled(cfg))
    entries = load_history_map(args.history_map) if args.history_map else []
    db = Database(cfg.database)
    try:
        result = backfill_with_map(
            cfg, db, infos, entries, args.db,
            since=parse_utc(args.since) if args.since else None,
            until=parse_utc(args.until) if args.until else None,
            check_integrity=not args.skip_integrity_check,
        )
    finally:
        db.close()
    print(f"Gelesen: {result.rows_read}, neu gespeichert: {result.rows_inserted}, "
          f"Zeitraum: {result.first_ts} bis {result.last_ts}")
    return 0


def _cmd_verify(cfg: Config, args: argparse.Namespace) -> int:
    db = Database(cfg.database)
    try:
        report = verify_logged_events(args.db, db)
    finally:
        db.close()
    print(f"Protokollierte manuelle Eingriffe: {report.logged_manual}")
    print(f"  davon zugeordnet: {report.matched}")
    print(f"  Entität nicht aufgezeichnet: {report.untracked_entity}")
    print(f"  nicht zugeordnet: {len(report.unmatched)}")
    print(f"Manuelle Eingriffe ohne Logger-Ereignis: {report.extra_manual}")
    print(f"Trefferquote: {report.match_rate:.1%} (Soll: mindestens {args.min_match_rate:.0%})")
    for item in report.unmatched[:20]:
        print(f"  fehlt: {item}")
    return 0 if report.match_rate >= args.min_match_rate else 1


def _cmd_purge(cfg: Config, args: argparse.Namespace) -> int:
    db = Database(cfg.database)
    try:
        rows = db.count_changes(args.entity_id)
        print(f"{rows} Zeilen für {args.entity_id} werden endgültig gelöscht.")
        if not args.yes and input("Wirklich löschen? [ja/NEIN] ").strip().lower() != "ja":
            print("Abgebrochen.")
            return 1
        db.purge_entity(args.entity_id)
        print("Gelöscht.")
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.command == "run":
            _token()
            asyncio.run(_run(cfg))
            return 0
        if args.command == "backfill":
            return _cmd_backfill(cfg, args)
        if args.command == "verify":
            return _cmd_verify(cfg, args)
        return _cmd_purge(cfg, args)
    except (ConfigError, FileNotFoundError, LabelNotFound, LabelEmpty, HassAuthError, HassConnectionError,
            UnsupportedSchema, CorruptDatabase) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Tests laufen lassen**

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_cli.py tests/test_healthcheck.py -v`
Expected: PASS (8 Tests).

- [ ] **Step 6: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS.

```bash
git add ai/collector/cli.py ai/collector/__main__.py ai/collector/healthcheck.py ai/tests/test_cli.py ai/tests/test_healthcheck.py
git commit -m "feat(ai): Kommandozeile (run, backfill, verify, purge-entity) und Healthcheck"
```

---

### Task 11: Container und Dokumentation

**Files:**
- Create: `ai/Dockerfile`, `ai/docker-compose.yml`, `ai/.env.example`, `ai/README.md`
- Modify: `ai/.gitignore` (Ordner `snapshots/` ergänzen)

**Interfaces:**
- Consumes: alle Befehle aus Aufgabe 10.
- Produces: ein startbares Image `python -m collector run`; Datenablage `./data`, Sicherung `./backup`, Regeln `./config` (nur lesend).

- [ ] **Step 1: Container-Dateien schreiben**

`ai/Dockerfile`:

```dockerfile
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collector ./collector
COPY sql ./sql
CMD ["python", "-m", "collector", "run"]
```

`ai/docker-compose.yml`:

```yaml
services:
  collector:
    build: .
    container_name: home-ai-collector
    restart: unless-stopped
    env_file: .env
    environment:
      COLLECTOR_CONFIG: /config/collector.yaml
      HEALTH_FILE: /tmp/collector.health
      TZ: Europe/Berlin
    volumes:
      - ./data:/data
      - ./backup:/backup
      - ./config:/config:ro
    healthcheck:
      test: ["CMD", "python", "-m", "collector.healthcheck"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 90s
```

`ai/.env.example`:

```
# Long-Lived Access Token: Home Assistant -> Profil -> Sicherheit -> Langlebige Zugriffstoken
HA_TOKEN=hier-das-token-eintragen
# Adresse von Home Assistant (WebSocket)
HA_URL=ws://homeassistant.local:8123/api/websocket
```

An `ai/.gitignore` anhängen:

```
snapshots/
```

- [ ] **Step 2: README schreiben**

`ai/README.md`:

````markdown
# KI-Datenbasis für Home Assistant

Zeichnet Zustandsänderungen ausgewählter Entitäten dauerhaft in SQLite auf (`home_ai.sqlite`), unabhängig von den 180 Tagen des Home-Assistant-Recorders. Grundlage für die KI-gestützte Heizungssteuerung. Spezifikation: `docs/superpowers/specs/2026-10-13-home-ai-data-foundation-design.md`.

## Welche Entitäten werden aufgezeichnet?

Genau die, die in Home Assistant das Label **`KI_DB_Sync`** tragen. Es gibt keine zweite Liste. Ein Label setzt du in der Oberfläche unter Einstellungen → Geräte & Dienste → Entitäten (Entität öffnen → Label). Raumgebundene Entitäten brauchen einen Bereich, daraus wird der Raum.

Entfernst du das Label, endet die Aufzeichnung innerhalb von 10 Minuten (`active = 0`, `tracked_until` wird gesetzt). Die bisherigen Daten bleiben erhalten. Endgültig löschen:

```bash
docker compose exec collector python -m collector purge-entity sensor.beispiel
```

## Regeln

`config/collector.yaml` enthält nur Regeln (Schwellen, Whitelist je Art, Ausnahmen je Entität), keine Entitäten.

## Starten

```bash
cp .env.example .env     # Token und Adresse eintragen
docker compose up -d --build
docker compose logs -f collector
```

Nach dem Start erscheint `Registry-Abgleich: N Entitäten mit Label KI_DB_Sync`. Trägt keine Entität das Label, beendet sich der Collector mit einer Fehlermeldung.

## Alte Daten einspielen (nur lesend)

1. Im Proxmox Backup Server den Snapshot der Home-Assistant-VM wählen, „File Restore“ öffnen und auf der Datenpartition `home-assistant_v2.db` samt `-wal` und `-shm` herunterladen (HAOS: `/mnt/data/supervisor/homeassistant/`).
2. Die Dateien in einen eigenen Ordner unter `snapshots/` legen (liegt nicht im Repository).
3. Einspielen (Ordner schreibbar einbinden, SQLite öffnet die Datei trotzdem nur lesend):

```bash
docker compose run --rm -v "$PWD/snapshots:/snapshots" collector \
  python -m collector backfill --db /snapshots/april/home-assistant_v2.db --history-map /config/history_map.yaml
```

Mehrfaches Einspielen derselben oder überlappender Dateien ist unschädlich.

## Prüfen

```bash
docker compose run --rm -v "$PWD/snapshots:/snapshots" collector \
  python -m collector verify --db /snapshots/april/home-assistant_v2.db
```

Vergleicht die abgeleiteten manuellen Eingriffe (`manual_actions`) mit den Ereignissen `thermostat_actor_logged` aus derselben Datenbank. Erwartet: Trefferquote mindestens 95 %.

## Datenbank ansehen

```bash
docker compose exec collector python -c "import sqlite3; c=sqlite3.connect('/data/home_ai.sqlite'); print(c.execute('SELECT entity_id, active, tracked_since FROM entities ORDER BY entity_id').fetchall())"
```

## Sicherung

Der Collector kopiert die Datenbank täglich um 03:30 Uhr nach `./backup/home_ai.sqlite`. Dieser Ordner lässt sich vom Proxmox Backup Server mitsichern.

## Hinweise

- Zeitstempel sind UTC.
- Lücken (Verbindungsabbruch, Neustart) werden aus der History-API von Home Assistant nachgeholt, höchstens 180 Tage zurück. Diese Zeilen haben `source = 'gap:history_api'` und tragen keinen Benutzerkontext, daher erscheinen manuelle Eingriffe während einer Lücke nicht in `manual_actions`.
- Tokens und Backups gehören nicht ins Repository.
````

- [ ] **Step 3: Log-Zeile für den Registry-Abgleich ergänzen**

In `ai/collector/live.py` in `Collector.sync_registry` direkt vor der Zeile `self._first_sync = False` einfügen:

```python
        log.info("Registry-Abgleich: %d Entitäten mit Label %s", len(rules), self.cfg.label)
```

Run: `cd ai && .venv/Scripts/python -m pytest tests/test_live.py -q`
Expected: PASS (15 Tests).

- [ ] **Step 4: Image bauen**

Run: `cd ai && docker build -t home-ai-collector .`
Expected: `Successfully built` beziehungsweise `naming to docker.io/library/home-ai-collector`. Falls auf diesem Rechner kein Docker läuft, den Bau in Aufgabe 12 auf dem Docker-Host ausführen und hier abhaken.

- [ ] **Step 5: Gesamtlauf und Commit**

Run: `cd ai && .venv/Scripts/python -m pytest -q`
Expected: alle Tests PASS.

```bash
git add ai/Dockerfile ai/docker-compose.yml ai/.env.example ai/README.md ai/.gitignore ai/collector/live.py
git commit -m "feat(ai): Container, Compose-Datei und README"
```

---

### Task 12: Einrichtung und Abnahme in der echten Instanz

Diese Aufgabe verändert deine Home-Assistant-Instanz (Label, Zuordnungen) und läuft auf deinem Docker-Host. Schritte mit **[du]** musst du selbst ausführen. Für Schritte mit **[MCP]** frage ich vor der Ausführung nach deiner Bestätigung. Entscheidet ein Ergebnis über den weiteren Verlauf, steht das als **Halt** dabei.

**Files:**
- Create: `ai/config/history_map.yaml`
- Modify: keine Quelldateien (außer `history_map.yaml`)

**Interfaces:**
- Consumes: den gesamten Code aus den Aufgaben 1 bis 11 und die Erfolgskriterien 1 bis 5 der Spezifikation.
- Produces: laufender Collector, befüllte `home_ai.sqlite` mit der letzten Heizsaison, bestandene Abnahmeprüfungen.

- [ ] **Step 1: [MCP] Label anlegen und dem Startbestand zuweisen**

Zuerst die Schemas der benötigten MCP-Werkzeuge laden (`ToolSearch` mit `select:mcp__11ac3993-bb2f-4f73-bb5a-286e03fc3658__ha_config_set_label,mcp__11ac3993-bb2f-4f73-bb5a-286e03fc3658__ha_set_entity,mcp__11ac3993-bb2f-4f73-bb5a-286e03fc3658__ha_get_entity`). Dann das Label `KI_DB_Sync` mit `ha_config_set_label` anlegen und es per `ha_set_entity` (Parameter für Labels laut geladenem Schema) diesen 36 Entitäten zuweisen:

```
climate.better_thermostat_livingroom, climate.better_thermostat_bedroom,
climate.better_thermostat_bathroom, climate.better_thermostat_office,
person.fabian, person.svenja,
input_select.flat_occupation_state, input_button.sleep_mode,
input_boolean.someone_in_bed, binary_sensor.office_day_template,
input_boolean.heating_on_off, input_boolean.heating_automation,
schedule.heating_schedule_livingroom, schedule.heating_schedule_bathroom, schedule.heating_schedule_office,
binary_sensor.presence_office_presence, binary_sensor.presence_kitchen_presence, binary_sensor.front_door_contact,
sensor.fabians_s22_presence_tracker_area_last_seen,
binary_sensor.windows_livingroom_and_door, binary_sensor.windows_bedroom,
binary_sensor.window_bathroom_contact, binary_sensor.window_office_balcony_door_contact,
binary_sensor.window_office_balcony_door_bottom_contact,
sensor.environment_sensor_livingroom_temperature, sensor.environment_sensor_livingroom_humidity,
sensor.environment_sensor_bedroom_temperature, sensor.environment_sensor_bedroom_humidity,
sensor.environment_sensor_bathroom_temperature, sensor.environment_sensor_bathroom_humidity,
sensor.environment_sensor_kitchen_temperature, sensor.environment_sensor_kitchen_humidity,
sensor.environment_sensor_outside_temperature, sensor.environment_sensor_outside_humidity,
sensor.office_air_quality_sensor_temperature, weather.home
```

Expected: alle 36 Zuweisungen melden Erfolg. **Halt:** Meldet `ha_set_entity` bei einer Entität, dass sie keine eindeutige Kennung hat (z. B. YAML-Template ohne `unique_id`), diese Entität auflisten und mit dir klären, ob sie entfällt oder in Home Assistant eine Kennung bekommt (Spezifikation 5.2, Einschränkung von Labels).

- [ ] **Step 2: [MCP] Bereiche prüfen**

Für die raumgebundenen Entitäten (Thermostate, Präsenz, Fenster, Umgebungssensoren) per `ha_get_entity` den Bereich auslesen und die Räume notieren.

Expected: jede raumgebundene Entität hat einen Bereich. Fehlt einer, mit dir klären, welcher Bereich es sein soll, und ihn nach Bestätigung zuweisen (Der Collector nimmt Entitäten ohne Bereich auf, warnt aber).

- [ ] **Step 3: [du] Token anlegen und Container starten**

1. In Home Assistant ein Long-Lived Access Token anlegen (Profil → Sicherheit) und in `ai/.env` als `HA_TOKEN` eintragen, `HA_URL` anpassen.
2. Den Ordner `ai/` auf den Docker-Host bringen (z. B. Repository klonen) und dort ausführen:

```bash
cd ai && cp .env.example .env && docker compose up -d --build
docker compose logs --tail=50 collector
```

Expected: Zeile `Registry-Abgleich: 36 Entitäten mit Label KI_DB_Sync` (oder die Anzahl nach Schritt 1) und keine Fehlermeldung. `docker compose ps` zeigt nach etwa zwei Minuten `healthy`.

- [ ] **Step 4: Live-Aufzeichnung prüfen (Erfolgskriterium 1)**

Run (auf dem Docker-Host, nach etwa fünf Minuten):

```bash
docker compose exec collector python -c "import sqlite3; c=sqlite3.connect('/data/home_ai.sqlite'); print(c.execute('SELECT COUNT(*) FROM entities WHERE active=1').fetchone(), c.execute('SELECT source, COUNT(*) FROM state_changes GROUP BY source').fetchall())"
```

Expected: `(36,)` aktive Entitäten und Zeilen mit `live` und `gap:history_api`. **Halt:** Steht dort `0` Zeilen insgesamt, `docker compose logs collector` lesen und die Ursache klären, bevor es weitergeht.

- [ ] **Step 5: Annahme zum Benutzerkontext prüfen (Risiko aus Spezifikation 11)**

1. [du] In der Home-Assistant-App den Sollwert des Büro-Thermostats auf einen anderen Wert stellen und etwa 40 Sekunden warten.
2. Run:

```bash
docker compose exec collector python -c "import sqlite3; c=sqlite3.connect('/data/home_ai.sqlite'); [print(tuple(r)) for r in c.execute('SELECT ts_utc, entity_id, changed_attrs, actor_class, actor_user_id FROM manual_actions ORDER BY ts_utc DESC LIMIT 5')]"
```

Expected: eine Zeile für `climate.better_thermostat_office` mit `temperature` und `actor_class = manual` und deiner Benutzer-ID. **Halt:** Steht dort `automation` oder `unknown`, trägt Better Thermostat die Benutzer-ID nicht im Kontext weiter. Dann ist die Klassifizierung aus 6.3 nicht tragfähig; den Befund (Kontext-IDs der beiden Zeilen aus `state_changes`) festhalten und mit dir entscheiden, wie der Verursacher stattdessen bestimmt wird, bevor der Backfill läuft.

- [ ] **Step 6: Annahme zur History-API prüfen**

Run:

```bash
docker compose exec collector python -c "import sqlite3; c=sqlite3.connect('/data/home_ai.sqlite'); print(c.execute(\"SELECT COUNT(*), SUM(context_id IS NOT NULL) FROM state_changes WHERE source='gap:history_api'\").fetchone())"
```

Expected: `(N, 0)`, also Zeilen aus dem Lückenabgleich ohne Kontext. Ist die zweite Zahl größer als 0, liefert die History-API doch Kontext; das ist nur eine Verbesserung, die README-Hinweis (Abschnitt „Hinweise“) entsprechend anpassen.

- [ ] **Step 7: [du] Snapshots holen**

Nach Abschnitt 8 der Spezifikation: im Proxmox Backup Server die Snapshots wählen (einen von Mitte April 2026, einen von Oktober oder November 2025), „File Restore“, `home-assistant_v2.db` mit `-wal` und `-shm` herunterladen und in `ai/snapshots/april/` beziehungsweise `ai/snapshots/november/` auf dem Docker-Host ablegen.

Expected: pro Ordner mindestens `home-assistant_v2.db`.

- [ ] **Step 8: Zeitpunkt der Better-Thermostat-Einführung und Räume bestimmen**

1. Räume der aktuellen Entitäten aus der Datenbank lesen:

```bash
docker compose exec collector python -c "import sqlite3; c=sqlite3.connect('/data/home_ai.sqlite'); print(c.execute(\"SELECT entity_id, room FROM entities WHERE entity_id LIKE 'climate.%'\").fetchall())"
```

2. Erste Änderung der Better-Thermostat-Entitäten in der alten Datenbank:

```bash
docker compose run --rm -v "$PWD/snapshots:/snapshots" collector python -c "
import sqlite3, datetime
c = sqlite3.connect('file:/snapshots/april/home-assistant_v2.db?mode=ro', uri=True)
rows = c.execute(\"SELECT sm.entity_id, MIN(s.last_updated_ts) FROM states s JOIN states_meta sm ON sm.metadata_id = s.metadata_id WHERE sm.entity_id LIKE 'climate.better_thermostat_%' GROUP BY sm.entity_id\").fetchall()
for entity_id, ts in rows:
    print(entity_id, datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat() if ts else None)
"
```

Expected: je Better-Thermostat-Entität ein Datum. Das früheste Datum ist der Zeitpunkt, bis zu dem die direkten `climate.trv_*` die Quelle waren.

- [ ] **Step 9: `history_map.yaml` schreiben**

`ai/config/history_map.yaml` mit den sechs direkten Thermostaten anlegen. `room` ist der Raumname aus Schritt 8.1 für den jeweiligen Raum, `valid_to` das früheste Datum aus Schritt 8.2 (Beispielwerte unten durch die ermittelten ersetzen):

```yaml
# Nur für den Backfill: frühere direkte Thermostate und ihre Räume.
entities:
  - {entity_id: climate.trv_livingroom_left,  room: wohnzimmer,   valid_to: "2025-11-01T00:00:00+00:00"}
  - {entity_id: climate.trv_livingroom_right, room: wohnzimmer,   valid_to: "2025-11-01T00:00:00+00:00"}
  - {entity_id: climate.trv_bedroom_left,     room: schlafzimmer, valid_to: "2025-11-01T00:00:00+00:00"}
  - {entity_id: climate.trv_bedroom_right,    room: schlafzimmer, valid_to: "2025-11-01T00:00:00+00:00"}
  - {entity_id: climate.trv_office,           room: buero,        valid_to: "2025-11-01T00:00:00+00:00"}
  - {entity_id: climate.trv_bathroom,         room: bad,          valid_to: "2025-11-01T00:00:00+00:00"}
```

Hinweis: Die Räume `wohnzimmer`, `schlafzimmer`, `buero`, `bad` und das Datum sind Beispiele und müssen mit den ermittelten Werten übereinstimmen. Danach das Verzeichnis `config/` auf dem Docker-Host aktualisieren.

- [ ] **Step 10: Backfill ausführen und auf Idempotenz prüfen (Erfolgskriterien 2 und 5)**

Run (auf dem Docker-Host, einmal je Snapshot, zuerst der frühere):

```bash
docker compose run --rm -v "$PWD/snapshots:/snapshots" collector \
  python -m collector backfill --db /snapshots/november/home-assistant_v2.db --history-map /config/history_map.yaml
docker compose run --rm -v "$PWD/snapshots:/snapshots" collector \
  python -m collector backfill --db /snapshots/april/home-assistant_v2.db --history-map /config/history_map.yaml
docker compose run --rm -v "$PWD/snapshots:/snapshots" collector \
  python -m collector backfill --db /snapshots/april/home-assistant_v2.db --history-map /config/history_map.yaml
```

Expected: die ersten beiden Läufe melden gelesene und neu gespeicherte Zeilen mit einem Zeitraum; der **dritte** Lauf (dieselbe Datei erneut) meldet `neu gespeichert: 0`. **Halt:** Meldet ein Lauf `Unbekanntes Schema` oder `beschädigt`, den Text lesen: Bei `beschädigt` `-wal`/`-shm` prüfen oder anderen Snapshot nehmen; bei `Unbekanntes Schema` die genannten fehlenden Spalten festhalten und mit dir besprechen.

Zeitraum der Daten prüfen:

```bash
docker compose exec collector python -c "import sqlite3; c=sqlite3.connect('/data/home_ai.sqlite'); print(c.execute('SELECT MIN(ts_utc), MAX(ts_utc) FROM state_changes').fetchone(), c.execute('SELECT source, from_ts, to_ts, rows FROM ingest_runs').fetchall())"
```

Expected: frühester Zeitstempel etwa im Oktober 2025 (Ende der Heizsaison 2025/26 bis April 2026 abgedeckt). Fehlen Wochen, sind sie in `ingest_runs` sichtbar; Lücken festhalten.

- [ ] **Step 11: Abgleich mit dem Logger (Erfolgskriterium 3)**

Run:

```bash
docker compose run --rm -v "$PWD/snapshots:/snapshots" collector \
  python -m collector verify --db /snapshots/april/home-assistant_v2.db
```

Expected: `Trefferquote: ... (Soll: mindestens 95%)` und Exit-Code 0. **Halt:** Liegt die Quote darunter, die ausgegebenen `fehlt:`-Zeilen ansehen. Häufige Ursachen: Zeitabweichung über 5 s, Eingriff über eine `climate.trv_*`, die nicht in `history_map.yaml` steht (zählt als „Entität nicht aufgezeichnet“), oder ein Benutzerkontext, der bei Better Thermostat verloren geht (siehe Schritt 5). Befund festhalten und mit dir entscheiden.

- [ ] **Step 12: Aufnahme einer neuen Entität prüfen (Erfolgskriterium 4)**

1. [MCP] Dem Sensor `sensor.environment_sensor_livingroom_pressure` das Label `KI_DB_Sync` zuweisen.
2. Nach höchstens 10 Minuten:

```bash
docker compose exec collector python -c "import sqlite3; c=sqlite3.connect('/data/home_ai.sqlite'); print(c.execute(\"SELECT entity_id, active, tracked_since FROM entities WHERE entity_id='sensor.environment_sensor_livingroom_pressure'\").fetchone())"
```

Expected: eine Zeile mit `active = 1` und einem `tracked_since`, das bis zu 180 Tage zurückliegen kann (nachgeholt aus der History-API). Danach das Label wieder entfernen, nach höchstens 10 Minuten zeigt dieselbe Abfrage `active = 0`; die Zeilen bleiben.

- [ ] **Step 13: Abschluss**

1. `history_map.yaml` committen (ohne Snapshots und Token):

```bash
git add ai/config/history_map.yaml
git commit -m "feat(ai): history_map für die frühere Heizsaison"
```

2. Erfolgskriterien 1 bis 5 aus Abschnitt 2 der Spezifikation einzeln mit Ergebnis abhaken und dir melden. Pushen nur nach deiner Bestätigung.

---

## Selbstprüfung (nach dem Schreiben)

**Spezifikationsabdeckung:**

| Spezifikation | Aufgabe |
|---------------|---------|
| 2 Erfolgskriterien 1–5 | Aufgabe 12 (Schritte 4, 10, 11, 12, 10) und Tests in 7, 8, 9 |
| 3 Entscheidungen (Docker, Python, SQLite, 30 s, Label, PBS, `ai/`) | 1, 3, 7, 10, 11 |
| 4 Architektur | 4–8 |
| 5.1 Startbestand (36 Entitäten) | 12.1 |
| 5.2 Label als Quelle, Raum, `domain`, nur Regeln in YAML, Einschränkung, History-Nachholen, `tracked_since`, Einrichtung | 1, 5, 7, 12 |
| 5.3 Entfernen (active, tracked_until, Daten bleiben, reaktivieren, purge) | 3, 7, 10 |
| 6.1 Schema | 3 |
| 6.2 Speicherregeln | 1, 2 |
| 6.3 View `manual_actions`, Klassen | 3 |
| 7.1 Collector (30 s, SIGTERM, Backoff, Lücke, Healthcheck, nächtliche Kopie) | 7, 10, 11 |
| 7.2 Backfill (nur lesend, Schemaprüfung, Filter, UNIQUE, `ingest_runs`, `history_map`) | 8 |
| 8 Snapshots holen | 12.7 |
| 9 Fehlerbehandlung | 4 (Auth, Verbindung), 7 (Label, Schreibfehler, Bereich), 8 (Schema, beschädigt) |
| 10 Tests (Regeln, Zuordnung, Entprellung, Idempotenz, Lücke, Abgleichtest, Rauchtest) | 2, 3, 7, 8, 9, 12 |
| 11 Risiken (Konsistenz, Aufbewahrung, BT-Datum, BT-Kontext, Datenmenge) | 8 (`quick_check`), 12.5, 12.8, 12.10 |
| 12 Beleuchtung vorbereitet (`domain`, `kind`, Whitelist je kind in YAML, neutrale Namen) | 1, 3 |
| 13 Verzeichnisstruktur | Dateistruktur oben |

**Bewusste Abweichungen von der Spezifikation, hier festgehalten:**
1. `room_map.valid_from` ist `NOT NULL DEFAULT ''` statt nullable, damit der eindeutige Schlüssel mit „seit jeher“ funktioniert (Aufgabe 3).
2. Backfill unterstützt nur das aktuelle Home-Assistant-Schema (Zeitstempel- und `*_bin`-Spalten); ältere Schemata brechen mit klarer Meldung ab. Die Snapshots von Oktober 2025 bis April 2026 liegen im aktuellen Schema.
3. Die Backfill-Menge umfasst auch Einträge der `history_map.yaml` (Spezifikation in Aufgabe 8 Schritt 10 präzisiert).
4. Lücken aus der History-API haben keinen Benutzerkontext (Annahme, in Aufgabe 12 Schritt 6 geprüft).
5. Die Abnahmeschwelle des Abgleichs mit dem Logger ist auf 95 % festgelegt (die Spezifikation nennt „reproduziert“ ohne Zahl).

**Platzhalter-Prüfung:** Die Beispielwerte in Aufgabe 12 Schritt 9 (`wohnzimmer`, Datum) sind ausdrücklich als zu ersetzende Messwerte gekennzeichnet, weil sie erst aus der laufenden Instanz entstehen. Sonst enthält der Plan keine offenen Stellen.

**Typkonsistenz:** `Change`, `EntityRule`, `EntityInfo`, `Config` werden in Aufgabe 1 definiert und überall mit denselben Feldnamen genutzt. `ChangeFilter.feed/tick/flush_pending` (2) werden von `Pipeline` (6) und `backfill_ha_db` (8) aufgerufen. `Database.insert_changes(..., commit=...)` (3) wird von `Pipeline` (Standard) und Backfill (`commit=False`) genutzt. `HassClient.command/subscribe/seconds_since_last_message` (4) werden von `registry` (5) und `Collector` (7) genutzt. `FakeHass.close_after_command` (4) wird in Aufgabe 7 genutzt. `open_readonly` (8) wird von `verify` (9) genutzt.
