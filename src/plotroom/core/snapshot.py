"""Budgeted rendering of the project board and next-item pages (AD-13)."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict
from typing import Any

from plotroom.core.claims import OVERLAP_KINDS, Claim
from plotroom.core.envelope import ok
from plotroom.core.events import LedgerError
from plotroom.core.github import GitHubSummary
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
    github: GitHubSummary | None = None,
    github_error: str | None = None,
    claim_count: int = 0,
    trigger_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    listed: list[dict[str, Any]] = []
    board: dict[str, Any] = {
        "project": project_id,
        "cursor": cursor,
        "lanes": lanes,
        "open_items": dict(Counter(item.kind for item in items)),
        "claims": claim_count,
        "overlaps": sum(item.kind in OVERLAP_KINDS for item in items),
        "items": listed,
        "left_out": len(items),
    }
    if trigger_counts:
        board["triggers"] = trigger_counts
    if github is not None:
        board["open_prs"] = github.open_prs
        board["github"] = {"available": github_error is None, "as_of": github.as_of}
        if github_error is not None:
            board["github"]["error"] = github_error
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
    return _render_page(
        cursor,
        ((asdict(item), {"id": item.id, "kind": item.kind}) for item in items),
        "items",
        offset,
        budget,
        has_more=has_more,
    )


def render_claims(
    cursor: int,
    claims: list[Claim],
    offset: int,
    budget: int,
    *,
    has_more: bool,
) -> dict[str, Any]:
    return _render_page(
        cursor,
        ((claim.render(), {"lane": claim.lane, "glob": claim.glob}) for claim in claims),
        "claims",
        offset,
        budget,
        has_more=has_more,
        omit_after_rows=False,
    )


def _render_page(
    cursor: int,
    rows: Iterable[tuple[dict[str, Any], dict[str, Any]]],
    field: str,
    offset: int,
    budget: int,
    *,
    has_more: bool,
    omit_after_rows: bool = True,
) -> dict[str, Any]:
    page: list[dict[str, Any]] = []
    data: dict[str, Any] = {"cursor": cursor, field: page}
    for index, (row, minimal) in enumerate(rows, start=offset):
        data["next_offset"] = index + 1
        if _add(data, page, row, budget):
            continue
        if (not page or omit_after_rows) and _add(
            data, page, {**minimal, "details_omitted": True}, budget
        ):
            continue
        if not page:
            raise LedgerError("unavailable", "row identity exceeds the configured token budget")
        data["next_offset"] = index
        break
    else:
        if not has_more:
            data.pop("next_offset", None)
    return data
