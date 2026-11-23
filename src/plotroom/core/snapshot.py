"""Budgeted rendering of the current project board (AD-13)."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from typing import Any

from plotroom.core.events import LedgerError
from plotroom.core.next_items import Item

DEFAULT_SNAPSHOT_BUDGET = 2000


def estimate_tokens(text: str) -> int:
    """Approximate tokens by counting UTF-8 bytes in groups of four."""
    return (len(text.encode("utf-8")) + 3) // 4


def render_snapshot(
    project_id: str, cursor: int, items: list[Item], lanes: dict[str, int], budget: int
) -> dict[str, Any]:
    listed: list[dict[str, Any]] = []
    board = {
        "project": project_id,
        "cursor": cursor,
        "lanes": lanes,
        "open_items": dict(Counter(item.kind for item in items)),
        "items": listed,
        "left_out": len(items),
    }

    def size() -> int:
        return estimate_tokens(json.dumps(board, ensure_ascii=False))

    if size() > budget:
        raise LedgerError("unavailable", "snapshot summary exceeds the configured token budget")
    for item in items:
        listed.append(asdict(item))
        board["left_out"] = len(items) - len(listed)
        if size() > budget:
            listed.pop()
            board["left_out"] = len(items) - len(listed)
            break
    return board
