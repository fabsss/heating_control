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
