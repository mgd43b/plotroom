"""Per-project ticks and post-commit action dispatch."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta

from plotroom.core.actions import Action, finish_action, record_attempt
from plotroom.core.events import NewEvent, format_time
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.core.work_items import refresh_idle_workers
from plotroom.ports.clock import Clock
from plotroom.ports.work_items import WorkItem, WorkItemSource

type ActionHandler = Callable[[Action], Awaitable[None]]
log = logging.getLogger("plotroom.runtime")


class ProjectRuntime:
    def __init__(
        self,
        ledger: Ledger,
        clock: Clock,
        *,
        tick_seconds: int,
        action_attempts: int,
        handlers: Mapping[str, ActionHandler] | None = None,
        workers: frozenset[str] = frozenset(),
        work_items: WorkItemSource | None = None,
    ) -> None:
        self.ledger = ledger
        self.clock = clock
        self.interval = timedelta(seconds=tick_seconds)
        self.next_tick = clock.now() + self.interval
        self.action_attempts = action_attempts
        self.handlers: Mapping[str, ActionHandler] = handlers if handlers is not None else {}
        self.workers = workers
        self.work_items = work_items
        self._tasks: list[asyncio.Task[None]] = []

    async def tick_if_due(self) -> bool:
        now = self.clock.now()
        if now < self.next_tick:
            return False
        scheduled_at = format_time(self.next_tick)
        items: tuple[WorkItem, ...] | None = ()
        if self.work_items is not None:
            try:
                items = await self.work_items.ready_items()
            except (OSError, ValueError, TimeoutError) as exc:
                items = None  # keep the last committed mirror if the source is unavailable
                log.warning(
                    "could not refresh the project ticket tree",
                    extra={"project": self.ledger.project_id, "reason": str(exc)},
                )

        async def tick(uow: UnitOfWork) -> None:
            if not (await uow.append(NewEvent("core", "core.tick", scheduled_at))).created:
                return
            if items is not None:
                await uow.replace_work_items(items)
            await refresh_idle_workers(uow, self.workers)
            await self.ledger.watchers.reconcile(uow)

        await self.ledger.run("system", tick)
        # Keep the cadence after delays without replaying a burst of stale ticks.
        finished = max(now, self.clock.now())
        self.next_tick += ((finished - self.next_tick) // self.interval + 1) * self.interval
        return True

    async def run_one_action(self) -> bool:
        action = await self.ledger.pending_action()
        if action is None:
            return False
        if action.attempts >= self.action_attempts:
            await self.ledger.run("system", lambda uow: finish_action(uow, action, "failed"))
            return True
        failed = False
        try:
            await self.handlers[action.kind](action)
        except Exception:
            # Action payloads and exception text may be sensitive. The item points to the key.
            log.warning("action attempt failed", extra={"kind": action.kind})
            failed = True

        async def commit_attempt(uow: UnitOfWork) -> None:
            completed = await record_attempt(uow, action)
            if not failed:
                await finish_action(uow, completed, "done")
            elif completed.attempts >= self.action_attempts:
                await finish_action(uow, completed, "failed")

        await self.ledger.run("system", commit_attempt)
        return True

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._ticks()), asyncio.create_task(self._actions())]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        results = await asyncio.gather(*self._tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                log.error("project background task failed", exc_info=result)
        self._tasks.clear()

    async def _ticks(self) -> None:
        while True:
            await asyncio.sleep(max(0, (self.next_tick - self.clock.now()).total_seconds()))
            await self.tick_if_due()

    async def _actions(self) -> None:
        while True:
            committed = self.ledger.commit_signal
            if not await self.run_one_action():
                await committed.wait()
