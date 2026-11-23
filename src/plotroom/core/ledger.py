"""The ledger: the one write path into a project's event log (AD-2, AD-3, AD-14)."""

from __future__ import annotations

import asyncio
import copy
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from plotroom.core.actions import ACTION_PAGE_SIZE, Action, QueuedAction
from plotroom.core.briefs import BRIEF, BriefSettings
from plotroom.core.catalog import Catalog
from plotroom.core.claims import PR_CLAIM_OVERLAP, TICKET_TREE_MOVE, Claim, sync_items
from plotroom.core.commands import COMMAND_APPENDER, command_kind
from plotroom.core.events import (
    AppendResult,
    Event,
    LedgerError,
    NewEvent,
    format_time,
    is_json,
    validate_fields,
)
from plotroom.core.github import FetchCursor, FetchRetry, GitHubSummary, PullRequest
from plotroom.core.github_projection import GitHubResource
from plotroom.core.merge_controller import ACTIVE_MERGE_ITEMS, reconcile_merge_controller
from plotroom.core.merge_gate import GatePolicy, GateSnapshot, GateVerdict, evaluate_gate
from plotroom.core.next_items import Item
from plotroom.core.roles import OwnershipView, role_family
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.core.watchers import Watchers
from plotroom.core.writer import ProjectWriter
from plotroom.ports.clock import Clock
from plotroom.ports.search import MAX_SEARCH_LIMIT, MAX_SEARCH_QUERY_LENGTH, Search, SearchPage
from plotroom.ports.store import Board, Store, Transaction
from plotroom.ports.work_items import WorkItem, WorkItemSource


@dataclass(frozen=True)
class CommandResult:
    """What a command returned; ``replayed`` is true when it is the first call's stored result."""

    result: dict[str, Any]
    replayed: bool


def _check_role(role: str) -> str:
    try:
        return role_family(role)
    except ValueError as exc:
        raise LedgerError("invalid", str(exc)) from None


class Ledger:
    """One project's event log: typed appends and deduplicated commands, one transaction each."""

    def __init__(
        self,
        store: Store,
        catalog: Catalog,
        clock: Clock,
        *,
        watcher_roles: tuple[str, ...] = (),
        wait_cap: int = 240,
        github_repository: str | None = None,
        merge_gate_policy: GatePolicy | None = None,
        github_tree_path: str | None = None,
        session_workers: Mapping[str, str] | None = None,
        drain_enabled: bool = False,
        brief_settings: BriefSettings | None = None,
        live_merge_controller: bool = False,
        merge_clerk: Callable[[], Awaitable[bool]] | None = None,
        fixture_merge_enabled: bool = False,
        fixture_branch_updates: bool = False,
        search: Search | None = None,
    ) -> None:
        if search is not None and search.project_id != store.project_id:
            raise ValueError("a ledger and its search index must belong to the same project")
        self._store = store
        self._search = search
        self._catalog = catalog
        self._clock = clock
        self._writer = ProjectWriter(store)
        self.watchers = Watchers(watcher_roles)
        self._wait_cap = wait_cap
        self.github_repository = github_repository
        self._merge_gate_policy = merge_gate_policy or GatePolicy()
        self._github_tree_path = github_tree_path
        self._live_merge_controller = live_merge_controller
        self._merge_clerk = merge_clerk
        self.confirm_merge_gate: Callable[[GateSnapshot], Awaitable[GateSnapshot | None]] | None = (
            None
        )
        self._fixture_merge_enabled = fixture_merge_enabled
        self._fixture_branch_updates = fixture_branch_updates
        self.github_error = "waiting for the first GitHub poll" if github_repository else None
        self.session_workers = dict(session_workers or {})
        self._drain_enabled = drain_enabled
        self._brief_settings = brief_settings or BriefSettings()
        self._stopping = asyncio.Event()

    @property
    def commit_signal(self) -> asyncio.Event:
        return self._writer.commit_signal

    @property
    def merge_gate_policy(self) -> GatePolicy:
        return self._merge_gate_policy

    async def pending_action(self) -> Action | None:
        return await self._store.pending_action()

    async def pending_actions(
        self, *, after: int, skip_branch_updates: bool, skip_controller_merges: bool = False
    ) -> tuple[QueuedAction, ...]:
        return await self._store.pending_actions(
            after=after,
            limit=ACTION_PAGE_SIZE,
            skip_branch_updates=skip_branch_updates,
            skip_controller_merges=skip_controller_merges,
        )

    @property
    def project_id(self) -> str:
        return self._store.project_id

    async def search(self, text: str, kinds: tuple[str, ...] | None, limit: int) -> SearchPage:
        if self._search is None:
            raise LedgerError("unavailable", "the project search index is unavailable")
        if not text.strip() or len(text) > MAX_SEARCH_QUERY_LENGTH or "\x00" in text:
            raise LedgerError(
                "invalid", f"search text must contain 1 to {MAX_SEARCH_QUERY_LENGTH} characters"
            )
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise LedgerError("invalid", f"search limit must be between 1 and {MAX_SEARCH_LIMIT}")
        return await self._search.query(self.project_id, text.strip(), kinds, limit)

    def start(self) -> None:
        self._stopping.clear()
        self._writer.start()

    async def clean_disabled_merge_items(self) -> None:
        if not self._live_merge_controller:

            async def clear(uow: UnitOfWork) -> None:
                for kind in ACTIVE_MERGE_ITEMS:
                    for subject in sorted(await uow.active_item_subjects(kind)):
                        await uow.clear_item(kind, subject)

            await self.run("system", clear)

    async def initialize_overlaps(self) -> None:
        """Materialize conflicts from facts already stored before the daemon opened."""

        async def restore(uow: UnitOfWork) -> None:
            if self.github_repository is None:
                for kind in (PR_CLAIM_OVERLAP, TICKET_TREE_MOVE):
                    await sync_items(uow, kind, {})
            else:
                uow.pull_overlaps_changed()

        await self.run("system", restore)

    async def stop(self) -> None:
        """Finish the queued inputs and stop the writer; later inputs fail ``unavailable``."""
        self.stop_waiting()
        await self._writer.stop()

    def gate_availability(self, snapshot: GateSnapshot) -> GateSnapshot:
        return replace(snapshot, github_unavailable=self.github_error is not None)

    async def set_github_error(self, error: str | None) -> None:
        if self.github_error != error:
            self.github_error = error
            await self.refresh_merge_controller()

    async def refresh_merge_controller(self) -> None:
        if self._live_merge_controller:

            async def refresh(uow: UnitOfWork) -> None:
                uow.all_merge_pulls = True

            await self.run("system", refresh)

    async def _refresh_merge_controller(self, uow: UnitOfWork) -> None:
        if (
            self._live_merge_controller
            and self.github_repository is not None
            and (uow.all_merge_pulls or uow.merge_pull_numbers or uow.merge_check_heads)
        ):
            clerk = await self._merge_clerk() if self._merge_clerk is not None else False
            await reconcile_merge_controller(
                uow,
                self.github_repository,
                self.merge_gate_policy,
                github_unavailable=self.github_error is not None,
                clerk=clerk,
                stage_fixture_merges=self._fixture_merge_enabled,
                branch_updates=self._fixture_branch_updates,
            )

    async def _evaluate_triggers(self, tx: Transaction) -> None:
        if (leaves := self._catalog.trigger_leaves) is not None:
            uow = UnitOfWork(
                tx,
                self._catalog.events,
                self._clock,
                role="system",
                items=self._catalog.items,
                search=self._search,
            )
            await leaves.evaluate_open(uow)

    async def run[T](self, role: str, work: Callable[[UnitOfWork], Awaitable[T]]) -> T:
        """Run ``work`` as one input in one transaction; its appends are checked as ``role``."""
        _check_role(role)

        async def in_transaction(tx: Transaction) -> T:
            uow = UnitOfWork(
                tx,
                self._catalog.events,
                self._clock,
                role=role,
                items=self._catalog.items,
                search=self._search,
            )
            result = await work(uow)
            await uow.refresh_overlaps(self.github_repository, self._github_tree_path)
            await self._refresh_merge_controller(uow)
            await self._evaluate_triggers(tx)
            return result

        return await self._writer.submit(in_transaction)

    async def append(self, role: str, event: NewEvent) -> AppendResult:
        """Append one event as ``role``. A duplicate returns the stored event, ``created=False``."""
        _check_role(role)
        self._catalog.events.validate_identity(role, event)  # refuse before queueing
        return await self.run(role, lambda uow: uow.append(event))

    async def execute(
        self, role: str, command: str, request_id: str, args: dict[str, Any]
    ) -> CommandResult:
        """Run a mutating command once per ``(role, command, request_id)``.

        The first call runs the handler and records a ``core.<command>`` event with its arguments
        and result in the same transaction. A retry returns that result and runs nothing.
        A failed handler leaves nothing behind and the error reaches the caller.
        """
        family = _check_role(role)
        registered = self._catalog.commands.get(command)
        if registered is None:
            raise LedgerError("invalid", f"unknown command {command!r}")
        rule = self._catalog.writer_rules.rule_for(command, family)
        if rule is None:
            raise LedgerError("forbidden", f"role {role!r} may not execute {command}")
        if family == "drain" and not self._drain_enabled:
            raise LedgerError("forbidden", "no drain session is configured for this project")
        if not isinstance(request_id, str) or not request_id:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise LedgerError("invalid", f"{command}: request_id must be a non-empty string")
        if not isinstance(args, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise LedgerError("invalid", f"{command}: args must be an object")
        # One snapshot is validated, copied to the handler and recorded in the receipt, so a caller
        # mutating its dict while the handler runs changes none of the three.
        try:
            snapshot = copy.deepcopy(args)
        except RecursionError:
            raise LedgerError("invalid", f"{command}: arguments are nested too deeply") from None
        kind = command_kind(command)
        upstream_id = f"{role}:{request_id}"

        async def in_transaction(tx: Transaction) -> CommandResult:
            uow = UnitOfWork(
                tx,
                self._catalog.events,
                self._clock,
                role=role,
                append_role=COMMAND_APPENDER,
                items=self._catalog.items,
                search=self._search,
            )
            recorded = await uow.find_event("core", kind, upstream_id)
            receipt = recorded.fields if recorded is not None else None
            if receipt is None and registered.record_if is not None:
                receipt = await tx.find_noop_receipt(command, upstream_id)
            if receipt is None:
                try:
                    validate_fields(registered.args, snapshot, command)
                except RecursionError:
                    raise LedgerError(
                        "invalid", f"{command}: arguments are nested too deeply"
                    ) from None
            # A retry can supply different arguments. Authorize the actual target of the
            # receipt, so new arguments cannot make a previous target visible.
            target: dict[str, Any] = snapshot if receipt is None else receipt["args"]
            if rule.owns is not None:
                view = OwnershipView(role, uow.find_event, uow.find_item, uow.find_lane)
                if not await rule.owns(view, copy.deepcopy(target)):
                    raise LedgerError(
                        "forbidden", f"role {role!r} does not own the target of {command}"
                    )
            if receipt is not None:
                return CommandResult(receipt["result"], True)
            result = await registered.handler(uow, copy.deepcopy(snapshot))
            if not isinstance(result, dict) or not is_json(result):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise TypeError(f"command {command!r} must return a JSON object")
            await uow.refresh_overlaps(self.github_repository, self._github_tree_path)
            if registered.record_if is not None and not registered.record_if(result):
                await tx.insert_noop_receipt(
                    command, upstream_id, {"args": snapshot, "result": result}
                )
            else:
                await uow.append(
                    NewEvent(
                        source="core",
                        kind=kind,
                        upstream_id=upstream_id,
                        fields={
                            "role": role,
                            "request_id": request_id,
                            "args": copy.deepcopy(snapshot),
                            "result": result,
                        },
                        lane_id=snapshot["lane"] if registered.lane_scoped else None,
                    )
                )
            await self._refresh_merge_controller(uow)
            await self._evaluate_triggers(tx)
            return CommandResult(result, False)

        return await self._writer.submit(in_transaction)

    async def head_seq(self) -> int:
        """The last allocated ``seq``. Reads committed state, not through the writer."""
        return await self._store.head_seq()

    async def events_after(self, seq: int, limit: int = 100) -> list[Event]:
        """Committed events above ``seq``, ascending."""
        return await self._store.events_after(seq, limit)

    def stop_waiting(self) -> None:
        self._stopping.set()
        self.commit_signal.set()

    async def wait_for_item(
        self,
        role: str,
        cursor: int | None,
        seconds: float | None,
        progress: Callable[[float, float], Awaitable[None]],
    ) -> tuple[int, dict[str, Any] | None]:
        """Wait for the first change visible to the caller and return its resume cursor."""
        _check_role(role)
        if role == "drain" and not self._drain_enabled:
            raise LedgerError("forbidden", "no drain session is configured for this project")
        if cursor is not None and not 0 <= cursor <= 2**63 - 1:
            raise LedgerError("invalid", "cursor must be a nonnegative ledger sequence")
        if seconds is not None and (not math.isfinite(seconds) or seconds < 0):
            raise LedgerError("invalid", "timeout must be a nonnegative finite number")
        cap = float(self._wait_cap) if seconds is None else min(seconds, self._wait_cap)
        now = asyncio.get_running_loop().time
        started = now()
        until = started + cap
        position = await self.head_seq() if cursor is None else cursor
        interval = min(15, cap / 2)
        report_at = started
        with self.watchers.waiting(role):
            while True:
                if self._stopping.is_set():
                    raise LedgerError("unavailable", "the project is stopping")
                committed = self.commit_signal
                wake_at = until
                if role == "drain":
                    check_started = now()
                    moment = self._clock.now()
                    readiness = await self._store.outbox_readiness(format_time(moment))
                    head = readiness.head
                    change = None
                    # Pending work remains actionable when resuming from a saved cursor or
                    # after a lease expires. The wake contains no lease or delivery payload.
                    if readiness.ready_seq is not None:
                        change = {"kind": "outbox_ready", "seq": readiness.ready_seq}
                    if readiness.next_expiry is not None:
                        delay = datetime.fromisoformat(readiness.next_expiry) - moment
                        wake_at = min(until, check_started + delay.total_seconds())
                else:
                    head, change = await self._store.first_item_change(role, position)
                if position > head:
                    raise LedgerError("invalid", "cursor is ahead of the project ledger")
                if change is not None:
                    return max(position, int(change["seq"])), change
                # Advancing past invisible rows is safe because the read used one committed view.
                position = head
                current = now()
                if current >= until:
                    return position, None
                if current >= report_at:
                    await progress(current - started, cap)
                    report_at = current + interval
                try:
                    async with asyncio.timeout_at(min(wake_at, report_at)):
                        await committed.wait()
                except TimeoutError:
                    continue

    async def ready_work_items(self) -> tuple[WorkItem, ...]:
        return await self._store.ready_work_items()

    async def read_next(self, role: str, offset: int, limit: int) -> tuple[int, list[Item]]:
        return await self._store.read_next(role, offset, limit)

    async def read_open_item(self, role: str, item_id: str) -> Item | None:
        return await self._store.read_open_item(role, item_id)

    async def read_board(self, role: str) -> Board:
        return await self._store.read_board(role)

    async def snapshot_board(self, role: str) -> tuple[Board, GitHubSummary | None]:
        board, github, _, _ = await self.read_snapshot(role)
        return board, github

    async def snapshot_summary(
        self, role: str, *, checklist_lane: str | None = None
    ) -> tuple[Board, GitHubSummary | None, dict[str, int], int, str | None]:
        return await self._store.read_snapshot(
            role, self.github_repository, checklist_lane=checklist_lane
        )

    async def snapshot_with_triggers(
        self, role: str
    ) -> tuple[Board, GitHubSummary | None, dict[str, int]]:
        board, github, trigger_counts, _ = await self.read_snapshot(role)
        return board, github, trigger_counts

    async def read_snapshot(
        self, role: str
    ) -> tuple[Board, GitHubSummary | None, dict[str, int], int]:
        board, github, counts, claims, _ = await self.snapshot_summary(role)
        return board, github, counts, claims

    async def read_claims(
        self, role: str, lane: str | None, offset: int, limit: int
    ) -> tuple[int, list[Claim]]:
        return await self._store.read_claims(role, lane, offset, limit)

    async def brief(
        self, role: str, work_item: str, worker: str, source: WorkItemSource | None
    ) -> str:
        if _check_role(role) not in self._catalog.read_roles.get(BRIEF, ()):
            raise LedgerError("forbidden", "only the orchestrator may read a lane brief")
        if worker not in self._catalog.workers:
            raise LedgerError("not_found", "configured worker not found")
        if not work_item or not work_item.isprintable() or not work_item.strip():
            raise LedgerError("invalid", "work_item must be a nonempty printable reference")
        if source is None:
            raise LedgerError("unavailable", "a work-item source is not configured")
        try:
            source_text = await source.body(work_item)
        except OSError, ValueError:
            raise LedgerError("unavailable", "the work-item source could not be read") from None
        if source_text is None:
            raise LedgerError("not_found", "work-item text not found in the configured source")
        lane, claims = await self._store.read_brief_claims(worker)
        return self._brief_settings.render(work_item, worker, source_text, lane, claims)

    async def read_overlaps(
        self, role: str, lane: str | None, pr: int | None, offset: int, limit: int
    ) -> tuple[int, list[Item]]:
        return await self._store.read_overlaps(role, lane, pr, offset, limit)

    async def list_triggers(self, role: str, lane: str | None) -> list[dict[str, Any]]:
        if _check_role(role) != "orchestrator":
            raise LedgerError("forbidden", "only the orchestrator may list triggers")
        return [trigger.render() for trigger in await self._store.list_triggers(lane)]

    async def merge_gate(
        self,
        role: str,
        pr: int,
        *,
        expected_head_sha: str | None = None,
        expected_main_sha: str | None = None,
    ) -> GateVerdict:
        if _check_role(role) not in {"clerk", "orchestrator"}:
            raise LedgerError(
                "forbidden", "only the clerk and orchestrator may query the merge gate"
            )
        if not 1 <= pr <= 2**63 - 1:
            raise LedgerError("invalid", "pr must be a positive SQLite integer")

        async def current() -> GateSnapshot:
            return self.gate_availability(
                await self._store.read_merge_gate(self.github_repository, pr)
            )

        def judge(snapshot: GateSnapshot) -> GateVerdict:
            return evaluate_gate(
                snapshot,
                self._merge_gate_policy,
                self._clock.now(),
                expected_head_sha=expected_head_sha,
                expected_main_sha=expected_main_sha,
            )

        snapshot = await current()
        verdict = judge(snapshot)
        if verdict.verdict != "MERGE" or (expected_head_sha is None and expected_main_sha is None):
            return verdict
        confirm = self.confirm_merge_gate
        fresh = await confirm(snapshot) if confirm is not None else None
        if fresh is None:
            return judge(replace(snapshot, github_unavailable=True))

        # Local policy (freeze, branch ownership and availability) can change while reading.
        latest = await current()
        latest_verdict = judge(latest)
        if latest_verdict.verdict != "MERGE":
            return latest_verdict
        if latest.pull != snapshot.pull and (
            latest.pull is None
            or snapshot.pull is None
            or replace(latest.pull, as_of=snapshot.pull.as_of) != snapshot.pull
        ):
            return judge(replace(latest, github_unavailable=True))
        return judge(
            replace(
                fresh,
                freeze_reason=latest.freeze_reason,
                lane=latest.lane,
                earlier_pull=latest.earlier_pull,
                ambiguous_lane=latest.ambiguous_lane,
                github_unavailable=latest.github_unavailable,
            )
        )

    async def pull_request(self, repository: str, number: int) -> PullRequest | None:
        return await self._store.pull_request(repository, number)

    async def github_resource(
        self, repository: str, resource: str, scope: str = ""
    ) -> GitHubResource | None:
        return await self._store.github_resource(repository, resource, scope)

    async def github_pull_targets(self, repository: str) -> tuple[PullRequest, ...]:
        return await self._store.github_pull_targets(repository)

    async def fetch_cursor(self, repository: str, resource: str) -> FetchCursor | None:
        return await self._store.fetch_cursor(repository, resource)

    async def fetch_retry(self, repository: str, resource: str) -> FetchRetry | None:
        return await self._store.fetch_retry(repository, resource)
