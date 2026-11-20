"""Fail CI when .env.example drifts from app settings.

Every field of app.core.settings.Settings must have a matching KEY in
.env.example (uppercase name). Extra keys are allowed only for
compose/mock/postgres wiring (see COMPOSE_ONLY).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.settings import Settings

COMPOSE_ONLY = ("DB_PORT_HOST", "PG_", "MOCK_")


def example_keys(path: Path) -> set[str]:
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def main() -> int:
    example = Path(__file__).resolve().parent.parent / ".env.example"
    keys = example_keys(example)
    missing = [name.upper() for name in Settings.model_fields if name.upper() not in keys]
    extra = [
        key
        for key in sorted(keys)
        if key.upper() not in {name.upper() for name in Settings.model_fields}
        and not key.startswith(COMPOSE_ONLY)
    ]
    failed = False
    for name in missing:
        print(f"missing in .env.example: {name}")
        failed = True
    for key in extra:
        print(f"unknown key in .env.example (not a setting, not compose-only): {key}")
        failed = True
    if failed:
        return 1
    print(f".env.example OK ({len(keys)} keys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
