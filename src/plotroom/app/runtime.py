"""Per-project ticks and post-commit action dispatch."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import timedelta

from plotroom.core.actions import (
    BRANCH_UPDATE,
    CONTROLLER_MERGE,
    SWITCHBOARD_SEND,
    Action,
    finish_action,
    record_attempt,
)
from plotroom.core.branch_updates import (
    BranchUpdate,
    complete_branch_update,
    eligible_branch_update,
    stage_branch_update,
)
from plotroom.core.events import NewEvent, format_time
from plotroom.core.ledger import Ledger
from plotroom.core.merge_controller import MERGE_READY
from plotroom.core.merge_gate import (
    GateSnapshot,
    authorized_label,
    determine_main_verdict,
    evaluate_gate,
    load_gate_snapshot,
)
from plotroom.core.outbox import OutboxMessage
from plotroom.core.trigger_actions import (
    MARK,
    NOTIFY,
    PROPOSE_ARCHIVE,
    SEND,
    DeliveryRoute,
    apply_local_action,
)
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.core.work_items import refresh_idle_workers
from plotroom.ports.clock import Clock
from plotroom.ports.github_write import FixtureGitHubMerge, FixtureGitHubWrite
from plotroom.ports.work_items import WorkItem, WorkItemSource

type ActionHandler = Callable[[Action], Awaitable[None]]
log = logging.getLogger("plotroom.runtime")


class _BranchUpdateDeferred(Exception):
    """Roll back a no-op without waking the action runner again."""


class _FixtureMergeDeferred(Exception):
    """Keep an unready action queued without spinning on a no-op transaction."""


class ProjectRuntime:
    def __init__(
        self,
        ledger: Ledger,
        clock: Clock,
        *,
        tick_seconds: int,
        action_attempts: int,
        handlers: Mapping[str, ActionHandler] | None = None,
        delivery_routes: Mapping[str, DeliveryRoute] | None = None,
        workers: frozenset[str] = frozenset(),
        work_items: WorkItemSource | None = None,
        inputs: tuple[Callable[[], Awaitable[None]], ...] = (),
        fixture_github_write: FixtureGitHubWrite | None = None,
        fixture_github_merge: FixtureGitHubMerge | None = None,
    ) -> None:
        self.ledger = ledger
        self.clock = clock
        self.interval = timedelta(seconds=tick_seconds)
        self.next_tick = clock.now() + self.interval
        self.action_attempts = action_attempts
        self.handlers: Mapping[str, ActionHandler] = handlers if handlers is not None else {}
        self.delivery_routes = dict(delivery_routes or {})
        self.workers = workers
        self.work_items = work_items
        self.inputs = inputs
        self.fixture_github_write = fixture_github_write
        self._fixture_github_merge = (
            fixture_github_merge if ledger.merge_gate_policy.controller_merges is True else None
        )
        self._action_lock = asyncio.Lock()
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
            uow.all_merge_pulls = True

        await self.ledger.run("system", tick)
        # Keep the cadence after delays without replaying a burst of stale ticks.
        finished = max(now, self.clock.now())
        self.next_tick += ((finished - self.next_tick) // self.interval + 1) * self.interval
        return True

    async def run_one_action(self) -> bool:
        async with self._action_lock:
            after = 0
            while True:
                page = await self.ledger.pending_actions(
                    after=after,
                    skip_branch_updates=self.fixture_github_write is None,
                    skip_controller_merges=self._fixture_github_merge is None,
                )
                if not page:
                    return False
                for entry in page:
                    if await self._run_action(entry.action):
                        return True
                after = page[-1].position

    async def _run_action(self, action: Action) -> bool:
        pinned = action.kind in (BRANCH_UPDATE, CONTROLLER_MERGE)
        if action.attempts >= self.action_attempts and not pinned:
            await self.ledger.run("system", lambda uow: finish_action(uow, action, "failed"))
            return True
        failed = False
        try:
            if await self._dispatch(action):
                return True
        except _BranchUpdateDeferred, _FixtureMergeDeferred:
            return False
        except Exception:
            # Action payloads and exception text may be sensitive. The item points to the key.
            log.warning("action attempt failed", extra={"kind": action.kind})
            failed = True

        async def commit_attempt(uow: UnitOfWork) -> None:
            completed = await record_attempt(uow, action)
            if not failed:
                await finish_action(uow, completed, "done")
            # A fixture response may have been lost on the last allowed attempt.
            elif completed.attempts >= self.action_attempts and (
                not pinned or action.attempts >= self.action_attempts
            ):
                await finish_action(uow, completed, "failed")

        await self.ledger.run("system", commit_attempt)
        return True

    def _eligible_update(self, uow: UnitOfWork, snapshot: GateSnapshot) -> BranchUpdate | None:
        if self.ledger.github_error is not None:
            return None
        return eligible_branch_update(snapshot, self.ledger.merge_gate_policy, uow.now())

    async def stage_branch_update(self, number: int) -> bool:
        """Queue an eligible action using the same snapshot and policy as the dry gate."""
        if self.fixture_github_write is None:
            return False

        async def stage(uow: UnitOfWork) -> bool:
            snapshot = await load_gate_snapshot(uow, self.ledger.github_repository, number)
            update = self._eligible_update(uow, snapshot)
            return update is not None and await stage_branch_update(uow, update)

        return await self.ledger.run("system", stage)

    async def _dispatch_branch_update(self, action: Action) -> None:
        port = self.fixture_github_write
        repository = action.payload["repository"]
        number = action.payload["number"]
        head_sha = action.payload["head_sha"]
        if port is None or repository != self.ledger.github_repository:
            raise _BranchUpdateDeferred

        async def dispatch(uow: UnitOfWork) -> None:
            snapshot = await load_gate_snapshot(uow, repository, number)
            pull = snapshot.pull
            if snapshot.freeze_reason is not None:
                raise _BranchUpdateDeferred
            update = self._eligible_update(uow, snapshot)
            eligible = update is not None and update.key == action.key
            exhausted = action.attempts >= self.action_attempts
            # A crash may leave a fixture receipt before any local attempt is committed.
            recovery_only = not eligible or exhausted
            main_sha = update.main_sha if update is not None and not recovery_only else None
            # The fixture call and final guard share the writer turn. A freeze or
            # projection change cannot commit between the guard and this effect.
            try:
                receipt = await port.update_branch(
                    repository=repository,
                    number=number,
                    action_key=action.key,
                    expected_head_sha=head_sha,
                    expected_main_sha=main_sha,
                    recovery_only=recovery_only,
                )
            except Exception as exc:
                if not recovery_only:
                    raise
                log.warning("fixture branch update receipt lookup failed")
                raise _BranchUpdateDeferred from exc
            if receipt is None:
                if pull is not None and pull.complete and pull.state == "merged":
                    await finish_action(uow, action, "done")
                    return
                if exhausted and eligible:
                    await finish_action(uow, action, "failed")
                    return
                raise _BranchUpdateDeferred
            branch = action.payload.get("branch")
            if branch is None and pull is not None and pull.head_sha == head_sha:
                branch = pull.branch
            await complete_branch_update(uow, action, receipt, branch, recovery_only=recovery_only)

        await self.ledger.run("system", dispatch)

    async def _dispatch_fixture_merge(self, action: Action) -> None:
        port = self._fixture_github_merge
        policy = self.ledger.merge_gate_policy
        repository, number = action.payload["repository"], action.payload["number"]
        head, main = action.payload["head_sha"], action.payload["main_sha"]
        method = action.payload["method"]
        effect_key = action.payload["effect_key"]
        if port is None or repository != self.ledger.github_repository:
            raise _FixtureMergeDeferred

        async def dispatch(uow: UnitOfWork) -> None:
            local = self.ledger.gate_availability(await load_gate_snapshot(uow, repository, number))
            evidence = await port.merge_evidence(
                repository=repository,
                number=number,
                action_key=effect_key,
            )
            if evidence.applied is True:
                await finish_action(uow, action, "done")
                return

            pull = local.pull
            if pull is None or pull.state != "open" or pull.head_sha != head:
                await finish_action(uow, action, "done")
                return
            item = await uow.active_item(MERGE_READY, str(number))
            if (
                item is None
                or item.id != action.payload["item_id"]
                or method != policy.merge_method
            ):
                await finish_action(uow, action, "done")
                return
            now = uow.now()
            actual = evidence.snapshot
            if (
                self.ledger.github_error is not None
                or item.state != "open"
                or (item.context["head_sha"], item.context["main_sha"]) != (head, main)
                or actual is None
                or not evidence.revision
                or actual.pull is None
                or (actual.pull.repository, actual.pull.number) != (repository, number)
            ):
                raise _FixtureMergeDeferred

            for snapshot in (local, actual):
                if (
                    evaluate_gate(
                        snapshot,
                        policy,
                        now,
                        expected_head_sha=head,
                        expected_main_sha=main,
                    ).verdict
                    != "MERGE"
                    or determine_main_verdict(snapshot, policy, now).kind != "green"
                    or authorized_label(snapshot, policy, now, policy.fix_label) is not None
                ):
                    raise _FixtureMergeDeferred

            if action.attempts >= self.action_attempts:
                await finish_action(uow, action, "failed")
                return
            if not await port.merge(
                repository=repository,
                number=number,
                action_key=effect_key,
                expected_head_sha=head,
                expected_main_sha=main,
                method=policy.merge_method,
                evidence_revision=evidence.revision,
            ):
                raise _FixtureMergeDeferred
            await finish_action(uow, await record_attempt(uow, action), "done")

        await self.ledger.run("system", dispatch)

    async def _dispatch(self, action: Action) -> bool:
        """Dispatch the effect. Local effects record their attempt in the same commit."""
        if action.kind == BRANCH_UPDATE:
            await self._dispatch_branch_update(action)
            return True
        if action.kind == CONTROLLER_MERGE:
            await self._dispatch_fixture_merge(action)
            return True
        if action.kind == SEND:
            worker = action.payload["worker"]
            route = self.delivery_routes[worker]
            if route.channel == "switchboard":
                await self.handlers[SWITCHBOARD_SEND](
                    replace(
                        action,
                        kind=SWITCHBOARD_SEND,
                        payload={"to": route.destination, "body": action.payload["text"]},
                    )
                )
                return False

            async def stage(uow: UnitOfWork) -> None:
                await uow.insert_outbox(
                    OutboxMessage(
                        action.key,
                        worker,
                        route.destination,
                        action.payload["text"],
                        await uow.allocate_seq(),
                    )
                )

            await self._commit_local(action, stage)
            return True
        if action.kind in (NOTIFY, MARK, PROPOSE_ARCHIVE):

            async def apply(uow: UnitOfWork) -> None:
                await apply_local_action(uow, action, self.ledger.github_repository)

            await self._commit_local(action, apply)
            return True
        await self.handlers[action.kind](action)
        return False

    async def _commit_local(
        self, action: Action, apply: Callable[[UnitOfWork], Awaitable[None]]
    ) -> None:
        async def commit(uow: UnitOfWork) -> None:
            attempted = await record_attempt(uow, action)
            await apply(uow)
            await finish_action(uow, attempted, "done")

        await self.ledger.run("system", commit)

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._ticks()), asyncio.create_task(self._actions())]
        self._tasks.extend(asyncio.ensure_future(read()) for read in self.inputs)

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
