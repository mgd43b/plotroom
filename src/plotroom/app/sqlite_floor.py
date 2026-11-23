"""The SQLite floor (AD-4): older builds can corrupt a WAL database.

Fixed in 3.51.3, and backported to 3.44.6 and 3.50.7. Below that the server reports not ready and
never opens a project file.
"""

from __future__ import annotations

import sqlite3

FLOOR_TEXT = "3.51.3 or later (or 3.44.6 / 3.50.7 backports)"


def parse_version(text: str) -> tuple[int, int, int]:
    parts = [int(p) for p in text.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def meets_floor(version: str) -> bool:
    major, minor, patch = parse_version(version)
    if (major, minor) == (3, 44):
        return patch >= 6
    if (major, minor) == (3, 50):
        return patch >= 7
    return (major, minor, patch) >= (3, 51, 3)


def floor_problem(version: str | None = None) -> str | None:
    """``None`` when the SQLite in use meets the floor, else a sentence saying why not."""
    found = version if version is not None else sqlite3.sqlite_version
    if meets_floor(found):
        return None
    return f"SQLite {found} is below the floor ({FLOOR_TEXT}); use a uv-managed Python"
