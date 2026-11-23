"""Budgeted rendering of the project board and next-item pages (AD-13)."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from typing import Any

from plotroom.core.envelope import ok
from plotroom.core.events import LedgerError
from plotroom.core.lanes import Lane
from plotroom.core.next_items import Item

DEFAULT_SNAPSHOT_BUDGET = 2000
MIN_BOARD_BUDGET = 256


def estimate_tokens(text: str) -> int:
    """Approximate tokens by counting UTF-8 bytes in groups of four."""
    return (len(text.encode("utf-8")) + 3) // 4


def _fits(data: dict[str, Any], budget: int) -> bool:
    # The MCP SDK emits this indented tool envelope as text.
    return estimate_tokens(json.dumps(ok(data), ensure_ascii=False, indent=2)) <= budget


def _add(
    data: dict[str, Any], rows: list[dict[str, Any]], row: dict[str, Any], budget: int
) -> bool:
    rows.append(row)
    if _fits(data, budget):
        return True
    rows.pop()
    return False


def render_snapshot(
    project_id: str,
    cursor: int,
    items: list[Item],
    lanes: dict[str, int],
    budget: int,
    *,
    lane_details: list[Lane] | None = None,
    lane_offset: int = 0,
) -> dict[str, Any]:
    listed: list[dict[str, Any]] = []
    board: dict[str, Any] = {
        "project": project_id,
        "cursor": cursor,
        "lanes": lanes,
        "open_items": dict(Counter(item.kind for item in items)),
        "items": listed,
        "left_out": len(items),
    }
    page: list[dict[str, Any]] = []
    if lane_details:
        board["lane_details"] = page
        if lane_offset < len(lane_details):
            board["next_lane_offset"] = lane_offset
    if not _fits(board, budget):
        raise LedgerError("unavailable", "snapshot summary exceeds the configured token budget")
    if lane_details:
        for index, lane in enumerate(lane_details[lane_offset:], start=lane_offset):
            board["next_lane_offset"] = index + 1
            if not _add(board, page, lane.render(), budget) and not _add(
                board, page, {"id": lane.id, "details_omitted": True}, budget
            ):
                if not page:
                    raise LedgerError(
                        "unavailable", "lane summary exceeds the configured token budget"
                    )
                board["next_lane_offset"] = index
                break
        else:
            board.pop("next_lane_offset", None)
    for item in items:
        board["left_out"] = len(items) - len(listed) - 1
        if not _add(board, listed, asdict(item), budget):
            board["left_out"] += 1
            break
    return board


def render_next(
    cursor: int,
    items: list[Item],
    offset: int,
    budget: int,
    *,
    has_more: bool = False,
) -> dict[str, Any]:
    page: list[dict[str, Any]] = []
    data: dict[str, Any] = {"cursor": cursor, "items": page}
    for index, item in enumerate(items, start=offset):
        data["next_offset"] = index + 1
        if not _add(data, page, asdict(item), budget) and not _add(
            data, page, {"id": item.id, "kind": item.kind, "details_omitted": True}, budget
        ):
            data["next_offset"] = index
            break
    else:
        if not has_more:
            data.pop("next_offset", None)
    return data
