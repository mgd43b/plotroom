"""Branch identity for the merge controller's one-open-PR rule."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plotroom.core.github import PullRequest
    from plotroom.core.lanes import Lane
    from plotroom.core.unit_of_work import UnitOfWork
    from plotroom.ports.store import Transaction


def _matches(lane: Lane, branch: str) -> bool:
    if lane.state == "archived":
        return False
    if lane.branch_pattern is None:
        return branch == lane.branch
    return fnmatchcase(branch, lane.branch_pattern)


async def merge_lane_evidence(
    reader: Transaction | UnitOfWork, pull: PullRequest | None
) -> tuple[Lane | None, int | None, bool]:
    """Resolve the lane, its lowest earlier open PR, and any ambiguous identity."""
    if pull is None or pull.head_repository != pull.repository or not pull.branch:
        return None, None, False
    lanes = await reader.active_lanes()
    matches = [lane for lane in lanes if _matches(lane, pull.branch)]
    if len(matches) != 1:
        return None, None, bool(matches)
    lane = matches[0]
    if pull.state != "open":
        return lane, None, False
    earlier = min(
        (
            other.number
            for other in await reader.open_pulls(pull.repository)
            if other.number < pull.number
            and other.head_repository == pull.repository
            and other.branch
            and _matches(lane, other.branch)
        ),
        default=None,
    )
    return lane, earlier, False
