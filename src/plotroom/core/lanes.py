"""Persistent lane lifecycle and worker ownership."""

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any

from plotroom.core.events import LedgerError
from plotroom.core.roles import OwnershipView
from plotroom.core.work_items import refresh_idle_workers

if TYPE_CHECKING:
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

    def render(self) -> dict[str, Any]:
        return {**asdict(self), "work_items": list(self.work_items)}


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
    work_items: list[Any] = args["work_items"]
    if worker not in workers:
        raise LedgerError("invalid", "worker is not configured for this project")
    if not lane_id.strip() or len(lane_id.encode()) > 128 or not branch.strip():
        raise LedgerError("invalid", "lane must be 1-128 UTF-8 bytes and branch must be non-empty")
    if not all(isinstance(item, str) and item.strip() for item in work_items):
        raise LedgerError("invalid", "work_items must contain non-empty strings")
    if await uow.find_lane(lane_id) is not None:
        raise LedgerError("conflict", "lane id already exists")
    if await uow.worker_lane(worker) is not None:
        raise LedgerError("conflict", "worker already has a lane")
    if work_items and not set(work_items).isdisjoint(await uow.claimed_work_items()):
        raise LedgerError("conflict", "one or more work items are already assigned")
    lane = Lane(lane_id, worker, tuple(work_items), branch)
    await uow.save_lane(lane)
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
        pr = await uow.pull_request(repository, args["pr"])
        if (
            pr is None
            or pr.state != "open"
            or not pr.complete
            or pr.branch != lane.branch
            or pr.head_sha != args["head_sha"]
        ):
            raise LedgerError("conflict", "the PR and head must match the open GitHub projection")
        result = await _save(
            uow, replace(lane, state="ready", waiting_on=None, pr=None, head_sha=None)
        )
        return {**result, "pr": pr.number, "head_sha": pr.head_sha}
    return await _save(
        uow, replace(lane, state="ready", waiting_on=None, pr=args["pr"], head_sha=args["head_sha"])
    )


async def archive_lane(
    uow: UnitOfWork, args: dict[str, Any], *, workers: frozenset[str]
) -> dict[str, Any]:
    lane = await _current(uow, args["lane"])
    result = await _save(uow, replace(lane, state="archived", waiting_on=None))
    await refresh_idle_workers(uow, workers)
    return result
