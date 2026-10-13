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
