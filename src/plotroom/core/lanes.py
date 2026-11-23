"""Persistent lane lifecycle and worker ownership."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from plotroom.core.checklists import (
    ARCHIVE_PROPOSAL,
    Checklist,
    activate_checklist,
    complete_checklist_step,
    declare_checklist,
    invalidate_checklist,
)
from plotroom.core.events import LedgerError, NewEvent
from plotroom.core.roles import OwnershipView
from plotroom.core.work_items import refresh_idle_workers

if TYPE_CHECKING:
    from plotroom.core.actions import Action
    from plotroom.core.unit_of_work import UnitOfWork

LANE_WAITING = "lane_waiting"


@dataclass(frozen=True)
class Lane:
    id: str
    worker: str
    work_items: tuple[str, ...]
    branch: str
    state: str = "active"
    waiting_on: str | None = None
    pr: int | None = None
    head_sha: str | None = None
    branch_pattern: str | None = None
    checklist: Checklist | None = None

    def with_checklist(self, checklist: Checklist | None) -> Lane:
        if checklist is None or checklist.state == "invalidated":
            return self
        if checklist.state == "active":
            return replace(self, checklist=checklist, pr=checklist.pr, head_sha=checklist.head_sha)
        return replace(self, checklist=checklist)

    def render(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "worker": self.worker,
            "work_items": list(self.work_items),
            "branch": self.branch,
            "state": self.state,
            "waiting_on": self.waiting_on,
            "pr": self.pr,
            "head_sha": self.head_sha,
            "branch_pattern": self.branch_pattern,
        }
        if self.checklist is not None:
            result["checklist"] = self.checklist.render()
        return result


def with_projected_pr(lane: Lane, number: int | None, head_sha: str | None) -> Lane:
    state = lane.state
    if state == "ready" and (number is None or (number, head_sha) != (lane.pr, lane.head_sha)):
        state = "active"
    return replace(lane, state=state, pr=number, head_sha=head_sha)


async def infer_lane(
    uow: UnitOfWork, *, worker: str | None = None, branch: str | None = None
) -> str | None:
    """Resolve an upstream binding against the current lane in the input transaction."""
    if worker is not None:
        lane = await uow.worker_lane(worker)
    else:
        lane = await uow.branch_lane(branch) if branch is not None else None
    return lane.id if lane is not None else None


async def owns_lane(view: OwnershipView, args: dict[str, Any]) -> bool:
    lane = await view.find_lane(args["lane"])
    return lane is not None and view.role == f"worker:{lane.worker}"


async def start_lane(
    uow: UnitOfWork, args: dict[str, Any], *, workers: frozenset[str]
) -> dict[str, Any]:
    lane_id: str = args["lane"]
    worker: str = args["worker"]
    branch: str = args["branch"]
    branch_pattern: str | None = args.get("branch_pattern")
    work_items: list[Any] = args["work_items"]
    if worker not in workers:
        raise LedgerError("invalid", "worker is not configured for this project")
    if not lane_id.strip() or len(lane_id.encode()) > 128 or not branch.strip():
        raise LedgerError("invalid", "lane must be 1-128 UTF-8 bytes and branch must be non-empty")
    if branch_pattern is not None and not branch_pattern.strip():
        raise LedgerError("invalid", "branch_pattern must be non-empty when supplied")
    if not all(isinstance(item, str) and item.strip() for item in work_items):
        raise LedgerError("invalid", "work_items must contain non-empty strings")
    if await uow.find_lane(lane_id) is not None:
        raise LedgerError("conflict", "lane id already exists")
    if await uow.worker_lane(worker) is not None:
        raise LedgerError("conflict", "worker already has a lane")
    if work_items and not set(work_items).isdisjoint(await uow.claimed_work_items()):
        raise LedgerError("conflict", "one or more work items are already assigned")
    lane = Lane(lane_id, worker, tuple(work_items), branch, branch_pattern=branch_pattern)
    await uow.save_lane(lane)
    uow.pull_overlaps_changed()
    await refresh_idle_workers(uow, workers)
    return lane.render()


async def _current(uow: UnitOfWork, lane_id: str) -> Lane:
    lane = await uow.find_lane(lane_id)
    if lane is None:
        raise LedgerError("not_found", "lane not found")
    if lane.state == "archived":
        raise LedgerError("conflict", "lane is archived")
    return lane


async def _save(uow: UnitOfWork, lane: Lane) -> dict[str, Any]:
    await uow.save_lane(lane)
    if lane.waiting_on == "orchestrator":
        await uow.raise_item(LANE_WAITING, lane.id, "orchestrator", lane.render())
    else:
        await uow.clear_item(LANE_WAITING, lane.id)
    return lane.render()


async def update_lane(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    lane = await _current(uow, args["lane"])
    return await _save(uow, replace(lane, waiting_on=args["waiting_on"] or None))


async def ready_lane(
    uow: UnitOfWork, args: dict[str, Any], *, repository: str | None
) -> dict[str, Any]:
    lane = await _current(uow, args["lane"])
    if not 0 < args["pr"] <= 2**63 - 1 or not args["head_sha"].strip():
        raise LedgerError("invalid", "pr must be positive and head_sha must be non-empty")
    if repository is not None:
        pr = await uow.open_branch_pull(repository, lane.branch)
        if pr is None or pr.number != args["pr"] or pr.head_sha != args["head_sha"]:
            raise LedgerError("conflict", "the PR and head must match the open GitHub projection")
    await declare_checklist(uow, lane, repository, args)
    return await _save(
        uow, replace(lane, state="ready", waiting_on=None, pr=args["pr"], head_sha=args["head_sha"])
    )


async def refresh_ready_lanes(uow: UnitOfWork, repository: str, branch: str) -> None:
    lanes = await uow.branch_lanes(branch)
    for lane in lanes:
        if lane.state != "ready":
            continue
        checklist = await uow.find_checklist(lane.id)
        pending = checklist if checklist is not None and checklist.state == "pending" else None
        if pending is None and len(lanes) != 1:
            continue  # The legacy lane binding requires an unambiguous branch.
        if pending is not None:
            if pending.repository != repository:
                continue
            projected = await uow.pull_request(repository, pending.pr)
            if projected is not None and projected.state == "merged" and pending.matches(projected):
                await activate_checklist(uow, pending)
                pending = None
        pr = await uow.open_branch_pull(repository, branch)
        if pr is None or (pr.number, pr.head_sha) != (lane.pr, lane.head_sha):
            await invalidate_checklist(uow, pending)
            await _save(uow, replace(lane, state="active", pr=None, head_sha=None))


async def archive_lane(
    uow: UnitOfWork, args: dict[str, Any], *, workers: frozenset[str]
) -> dict[str, Any]:
    lane = await _current(uow, args["lane"])
    checklist = await uow.find_checklist(lane.id)
    if checklist is not None and checklist.state == "active" and (steps := checklist.open_steps()):
        names = ", ".join(repr(step.step) for step in steps)
        raise LedgerError("conflict", f"the lane has open checklist steps: {names}")
    await invalidate_checklist(uow, checklist)
    result = await _save(uow, replace(lane, state="archived", waiting_on=None))
    await uow.delete_claims(lane.id)
    await uow.clear_item(ARCHIVE_PROPOSAL, lane.id)
    await refresh_idle_workers(uow, workers)
    return result


async def checklist_done(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    lane = await _current(uow, args["lane"])
    return await complete_checklist_step(uow, lane.id, args["step"], args.get("ref"))


async def mark_lane(uow: UnitOfWork, action: Action, *, repository: str | None) -> None:
    """Apply a system-owned mark using the normal current-lane and ready rules."""
    lane = await _current(uow, action.payload["lane"])
    state = action.payload["state"]
    if state == "active":
        await invalidate_checklist(uow, await uow.find_checklist(lane.id))
        await _save(uow, replace(lane, state="active"))
    elif state == "ready":
        pr, head = lane.pr, lane.head_sha
        if repository is not None and (pull := await uow.open_branch_pull(repository, lane.branch)):
            pr, head = pull.number, pull.head_sha
        if pr is None or head is None:
            raise LedgerError("conflict", "a ready lane requires a PR and head")
        await ready_lane(uow, {"lane": lane.id, "pr": pr, "head_sha": head}, repository=repository)
    else:
        raise LedgerError("invalid", "mark state must be active or ready")
    await uow.append(
        NewEvent(
            "core",
            "core.lane_marked",
            action.key,
            {"lane": lane.id, "state": state},
            lane_id=lane.id,
        )
    )
