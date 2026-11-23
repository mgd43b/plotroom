"""``snapshot()`` rendering. The tracer renders an empty board; entry 6 replaces it."""

from __future__ import annotations

from typing import Any

from plotroom.core.envelope import err, ok


def render_empty_snapshot(project_id: str) -> dict[str, Any]:
    """An empty board: nothing in any lane, no open items, nothing left out, cursor 0."""
    return {
        "project": project_id,
        "cursor": 0,
        "lanes": {},
        "open_items": {},
        "items": [],
        "left_out": 0,
    }


def snapshot_result(project_id: str, unavailable: str | None) -> dict[str, Any]:
    """The ``snapshot()`` tool result for the authenticated project."""
    if unavailable is not None:
        return err("unavailable", unavailable)
    return ok(render_empty_snapshot(project_id))
