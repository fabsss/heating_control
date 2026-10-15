from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

MARKER = "@@input:"


def load_blueprint(path: Path) -> dict:
    """Liest einen Blueprint ohne eigenen YAML-Lader: `!input name` wird zum Text `@@input:name`."""
    text = re.sub(r"!input\s+(\w+)", rf'"{MARKER}\1"', Path(path).read_text(encoding="utf-8"))
    return yaml.safe_load(text)


def resolve(value: Any, inputs: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith(MARKER):
        return inputs[value[len(MARKER):]]
    if isinstance(value, dict):
        return {key: resolve(item, inputs) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, inputs) for item in value]
    return value
