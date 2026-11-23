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


def snapshot_result(project_ids: tuple[str, ...], unavailable: str | None) -> dict[str, Any]:
    """The ``snapshot()`` tool result for a server serving ``project_ids``.

    Tokens (entry 5) will say which project a caller means. Until then only a server with exactly
    one project can answer; with several, guessing would cross a project boundary.
    """
    if unavailable is not None:
        return err("unavailable", unavailable)
    if len(project_ids) != 1:
        return err(
            "invalid",
            "several projects are registered and no credential selects one; "
            "register a single project until per-project tokens exist",
        )
    return ok(render_empty_snapshot(project_ids[0]))
