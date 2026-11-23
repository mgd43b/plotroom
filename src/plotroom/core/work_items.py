"""The durable ready-work mirror and its idle-worker condition."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

IDLE_WORKER = "idle_worker"


async def refresh_idle_workers(uow: UnitOfWork, workers: frozenset[str]) -> None:
    claimed = await uow.claimed_work_items()
    eligible = [item.ref for item in await uow.ready_work_items() if item.ref not in claimed]
    for worker in sorted(workers | await uow.active_item_subjects(IDLE_WORKER)):
        if worker in workers and eligible and await uow.worker_lane(worker) is None:
            await uow.raise_item(
                IDLE_WORKER,
                worker,
                "orchestrator",
                {"worker": worker, "work_items": eligible},
            )
        else:
            await uow.clear_item(IDLE_WORKER, worker)
