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


class MergeLaneIndex:
    """Bind branch patterns and first PRs once for one consistent repository view."""

    def __init__(self, lanes: list[Lane], pulls: tuple[PullRequest, ...]) -> None:
        self._lanes = lanes
        self._matches: dict[str, tuple[Lane, ...]] = {}
        self._first: dict[str, int] = {}
        for pull in pulls if lanes else ():
            if pull.head_repository != pull.repository or not pull.branch:
                continue
            for lane in self._matching(pull.branch):
                first = self._first.get(lane.id)
                if first is None or pull.number < first:
                    self._first[lane.id] = pull.number

    def _matching(self, branch: str) -> tuple[Lane, ...]:
        if branch not in self._matches:
            self._matches[branch] = tuple(lane for lane in self._lanes if _matches(lane, branch))
        return self._matches[branch]

    def evidence(self, pull: PullRequest | None) -> tuple[Lane | None, int | None, bool]:
        if pull is None or pull.head_repository != pull.repository or not pull.branch:
            return None, None, False
        matches = self._matching(pull.branch)
        if len(matches) != 1:
            return None, None, bool(matches)
        lane = matches[0]
        first = self._first.get(lane.id)
        earlier = (
            first if pull.state == "open" and first is not None and first < pull.number else None
        )
        return lane, earlier, False


async def merge_lane_evidence(
    reader: Transaction | UnitOfWork, pull: PullRequest | None
) -> tuple[Lane | None, int | None, bool]:
    """Resolve the same evidence for one ad hoc gate read."""
    if pull is None or pull.head_repository != pull.repository or not pull.branch:
        return None, None, False
    lanes = await reader.active_lanes()
    if not lanes:
        return None, None, False
    index = MergeLaneIndex(lanes, await reader.open_pulls(pull.repository))
    return index.evidence(pull)
