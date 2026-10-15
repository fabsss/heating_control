from __future__ import annotations

from datetime import datetime

from .db import Database


def update_origins(db: Database, now: datetime, *, rebuild: bool = False) -> int:
    return 0
