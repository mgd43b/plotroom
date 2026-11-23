"""The unit of work (AD-14): the handle given to one input's work inside the writer transaction.

Everything done through it commits or rolls back together. It is the extension point for later
consequences (adapter cursors, next items, triggers, actions).
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from plotroom.core.actions import Action
from plotroom.core.asks import reconcile_asks
from plotroom.core.budgets import BUDGET_ITEM, BUDGET_THRESHOLD, Budget
from plotroom.core.claims import Claim, reconcile_claim_pairs
from plotroom.core.events import (
    AppendResult,
    Event,
    EventRegistry,
    LedgerError,
    NewEvent,
    format_time,
    validate_fields,
)
from plotroom.core.github import PULL_REQUESTS, FetchCursor, FetchRetry, PullRequest
from plotroom.core.github_projection import (
    ASK_COMMENT_HISTORY,
    CHECKS,
    COMMENTS,
    ISSUES,
    LABELS,
    MAIN,
    MAIN_CI,
    PULL_TEXT,
    THREADS,
    GitHubResource,
)
from plotroom.core.lanes import Lane
from plotroom.core.merge_overlap import PULL_COMPARISON
from plotroom.core.next_items import Item, ItemKind
from plotroom.core.pull_overlaps import reconcile_pull_overlaps
from plotroom.core.roles import role_family
from plotroom.core.search_sources import (
    ask_record,
    comment_record,
    decision_record,
    pull_body_record,
    pull_text_record,
)
from plotroom.core.trigger_actions import SEND
from plotroom.ports.clock import Clock
from plotroom.ports.search import (
    BackfillPage,
    BackfillPosition,
    BackfillSource,
    EmbeddedRecord,
    EmbeddingIndex,
    Search,
    SearchBackfill,
    SearchRecord,
)
from plotroom.ports.store import Transaction
from plotroom.ports.work_items import WorkItem

if TYPE_CHECKING:
    from plotroom.core.ask_checks import Ask, IssueComment
    from plotroom.core.checklists import Checklist
    from plotroom.core.human_queue import Decision
    from plotroom.core.outbox import OutboxLease, OutboxMessage
    from plotroom.core.triggers import Trigger


def new_event_id() -> str:
    return f"evt_{uuid.uuid7()}"


class UnitOfWork:
    """One input's transaction, its role and the services its work may use.

    ``role`` is who the input comes from. ``append`` checks events against ``append_role``, which
    is the same role for a plain event input and ``system`` inside a command (core's own handling).
    """

    def __init__(
        self,
        tx: Transaction,
        events: EventRegistry,
        clock: Clock,
        *,
        role: str,
        items: Mapping[str, ItemKind] | None = None,
        append_role: str | None = None,
        id_factory: Callable[[], str] = new_event_id,
        search: Search | None = None,
    ) -> None:
        self._tx = tx
        self._events = events
        self._clock = clock
        self._role = role
        self._items: Mapping[str, ItemKind] = items if items is not None else {}
        self._append_role = append_role if append_role is not None else role
        self._id_factory = id_factory
        self._search = search
        self._claims_changed = False
        self._pulls_changed = False
        self.all_merge_pulls = False
        self.merge_pull_numbers: set[int] = set()
        self.merge_check_heads: set[str] = set()
        self.merge_observations: dict[int, tuple[PullRequest, bool]] = {}
        self._ask_repositories: set[str] = set()
        self._tick = False

    @property
    def role(self) -> str:
        return self._role

    def now(self) -> datetime:
        return self._clock.now()

    async def find_event(self, source: str, kind: str, upstream_id: str) -> Event | None:
        return await self._tx.find_event(source, kind, upstream_id)

    async def latest_event(
        self,
        kind: str,
        *,
        fields: Mapping[str, str | bool] | None = None,
        after: int = 0,
    ) -> Event | None:
        return await self._tx.latest_event(kind, fields=fields, after=after)

    async def latest_run_report(self, run_id: str) -> Event | None:
        return await self._tx.latest_run_report(run_id)

    async def lane_activity(self, lane: str) -> Event | None:
        return await self._tx.lane_activity(lane)

    async def find_claude_turn(self, session: str, aliases: tuple[str, ...]) -> Event | None:
        return await self._tx.find_claude_turn(session, aliases)

    async def bind_claude_turn(self, session: str, aliases: tuple[str, ...], event: str) -> None:
        await self._tx.bind_claude_turn(session, aliases, event)

    async def allocate_seq(self) -> int:
        """The project's next ``seq``; rolled back with the transaction."""
        return await self._tx.allocate_seq()

    async def save_embedding_batch(
        self,
        index: EmbeddingIndex,
        expected: int,
        through: int,
        records: tuple[EmbeddedRecord, ...],
    ) -> bool:
        if self._role != "system":
            raise LedgerError("forbidden", "only core may update the embedding index")
        return await index.commit(self._tx, expected, through, records)

    async def save_search_backfill(
        self,
        index: SearchBackfill,
        source: BackfillSource,
        expected: BackfillPosition,
        page: BackfillPage,
    ) -> bool:
        if self._role != "system":
            raise LedgerError("forbidden", "only core may update search backfill")
        return await index.commit_backfill(self._tx, source, expected, page)

    async def ready_work_items(self) -> tuple[WorkItem, ...]:
        return await self._tx.ready_work_items()

    async def replace_work_items(self, items: tuple[WorkItem, ...]) -> None:
        await self._tx.replace_work_items(items)

    async def claimed_work_items(self) -> set[str]:
        return await self._tx.claimed_work_items()

    async def find_lane(self, lane_id: str) -> Lane | None:
        return await self._tx.find_lane(lane_id)

    async def find_budget(self, run: str) -> Budget | None:
        return await self._tx.find_budget(run)

    async def insert_budget(self, budget: Budget) -> None:
        await self._tx.insert_budget(budget)

    async def update_budget_spend(self, budget: Budget) -> None:
        await self._tx.update_budget_spend(budget)

    async def has_routes(self) -> bool:
        return await self._tx.has_routes()

    async def find_route(self, action_kind: str) -> str | None:
        return await self._tx.find_route(action_kind)

    async def save_route(self, action_kind: str, lane: str) -> None:
        await self._tx.save_route(action_kind, lane)

    async def worker_lane(self, worker: str) -> Lane | None:
        return await self._tx.worker_lane(worker)

    async def branch_lane(self, branch: str) -> Lane | None:
        return await self._tx.branch_lane(branch)

    async def branch_lanes(self, branch: str) -> list[Lane]:
        return await self._tx.branch_lanes(branch)

    async def active_lanes(self) -> list[Lane]:
        return await self._tx.active_lanes()

    async def pull_request(self, repository: str, number: int) -> PullRequest | None:
        return await self._tx.pull_request(repository, number)

    async def open_branch_pull(self, repository: str, branch: str) -> PullRequest | None:
        return await self._tx.open_branch_pull(repository, branch)

    async def deploy_freeze(self) -> str | None:
        return await self._tx.deploy_freeze()

    async def set_deploy_freeze(self, reason: str | None) -> None:
        await self._tx.set_deploy_freeze(reason)
        self.all_merge_pulls = True

    async def open_pulls(self, repository: str) -> tuple[PullRequest, ...]:
        return await self._tx.open_pulls(repository)

    async def lane_claims(self, lane: str | None = None) -> list[Claim]:
        return await self._tx.lane_claims(lane)

    async def save_claims(self, claims: list[Claim]) -> None:
        await self._tx.save_claims(claims)
        self._claims_changed = True

    async def delete_claims(self, lane: str, globs: list[str] | None = None) -> None:
        await self._tx.delete_claims(lane, globs)
        self._claims_changed = True

    def pull_overlaps_changed(self) -> None:
        self._pulls_changed = True

    async def refresh_overlaps(self, repository: str | None, tree_path: str | None) -> None:
        if not self._claims_changed and not (self._pulls_changed and repository is not None):
            return
        claims = await self.lane_claims()
        if self._claims_changed:
            await reconcile_claim_pairs(self, claims)
        if repository is not None:
            await reconcile_pull_overlaps(self, repository, tree_path, claims)

    async def latest_merge_verdict(self, repository: str, number: int) -> Event | None:
        return await self._tx.latest_merge_verdict(repository, number)

    async def save_merge_verdict(self, repository: str, number: int, seq: int) -> None:
        await self._tx.save_merge_verdict(repository, number, seq)

    async def save_pull_request(self, pull: PullRequest, *, historical: bool = False) -> None:
        if pull.complete and pull.state == "merged" and not historical:
            prior = await self._tx.pull_request(pull.repository, pull.number)
            transition = prior is None or prior.state != "merged"
            if transition or (
                prior is not None and prior.merge_commit_sha != pull.merge_commit_sha
            ):
                self.merge_observations[pull.number] = (pull, transition)
        await self._tx.save_pull_request(pull)
        self.merge_pull_numbers.add(pull.number)
        self.all_merge_pulls = True  # branch membership may change the first PR on a lane

    async def github_resource(
        self, repository: str, resource: str, scope: str = ""
    ) -> GitHubResource | None:
        return await self._tx.github_resource(repository, resource, scope)

    async def save_github_resource(
        self, repository: str, resource: str, scope: str, result: GitHubResource
    ) -> None:
        await self._tx.save_github_resource(repository, resource, scope, result)
        if self._search is not None and result.complete:
            if resource == COMMENTS:
                for comment in result.records:
                    if comment.key == scope and comment.kind == "issue_comment":
                        await self.index_search_record(comment_record(repository, comment))
            elif resource == PULL_TEXT:
                pull = await self._tx.pull_request(repository, int(scope))
                if pull is not None and (record := pull_text_record(pull, result)) is not None:
                    await self.index_search_record(record)
        if resource in (MAIN, MAIN_CI):
            self.all_merge_pulls = True
        elif resource == CHECKS:
            self.merge_check_heads.add(scope)
        elif resource in (LABELS, THREADS, PULL_COMPARISON, PULL_TEXT):
            self.merge_pull_numbers.add(int(scope))
        if resource in {ISSUES, COMMENTS, ASK_COMMENT_HISTORY}:
            self._ask_repositories.add(repository)

    async def index_search_record(
        self, record: SearchRecord, *, only_if_absent: bool = False
    ) -> None:
        if self._search is not None:
            await self._search.index_record(self._tx, record, only_if_absent=only_if_absent)

    async def index_pull_body(self, repository: str, number: int, body: str) -> None:
        await self.index_search_record(pull_body_record(repository, number, body))

    async def fetch_cursor(self, repository: str, resource: str) -> FetchCursor | None:
        return await self._tx.fetch_cursor(repository, resource)

    async def save_fetch_cursor(self, repository: str, resource: str, cursor: FetchCursor) -> None:
        await self._tx.save_fetch_cursor(repository, resource, cursor)
        if resource == PULL_REQUESTS:
            self.all_merge_pulls = True

    async def ask_for_comment(self, repository: str, comment_id: int) -> Ask | None:
        return await self._tx.ask_for_comment(repository, comment_id)

    async def find_ask(self, ask_id: str) -> Ask | None:
        return await self._tx.find_ask(ask_id)

    async def save_ask(self, ask: Ask) -> None:
        await self._tx.save_ask(ask)
        await self.index_search_record(ask_record(ask))
        self._ask_repositories.add(ask.repository)

    async def unsettled_asks(self, repository: str) -> list[Ask]:
        return await self._tx.unsettled_asks(repository)

    async def ask_comments(
        self, repository: str, issues: set[int]
    ) -> dict[int, list[IssueComment]]:
        return await self._tx.ask_comments(repository, issues)

    async def ask_comment_histories(
        self, repository: str, issues: set[int]
    ) -> dict[int, tuple[str, frozenset[int]]]:
        return await self._tx.ask_comment_histories(repository, issues)

    async def refresh_asks(
        self, repository: str | None, human_login: str | None, stale_days: int
    ) -> None:
        if repository is not None and (self._tick or repository in self._ask_repositories):
            await reconcile_asks(self, repository, human_login, stale_days)

    async def save_fetch_retry(
        self, repository: str, resource: str, retry: FetchRetry | None
    ) -> None:
        await self._tx.save_fetch_retry(repository, resource, retry)

    async def save_lane(self, lane: Lane) -> None:
        await self._tx.save_lane(lane)
        self.all_merge_pulls = True

    async def find_checklist(self, lane: str) -> Checklist | None:
        return await self._tx.find_checklist(lane)

    async def save_checklist(self, checklist: Checklist) -> None:
        await self._tx.save_checklist(checklist)

    async def set_checklist_step_completion(
        self, lane: str, version: int, step: str, completion_ref: str
    ) -> bool:
        return await self._tx.set_checklist_step_completion(lane, version, step, completion_ref)

    async def enqueue_action(
        self,
        cause: Event,
        kind: str,
        payload: dict[str, Any],
        *,
        discriminator: str = "",
        stable_key: str | None = None,
    ) -> str:
        """Queue an effect in its causing transaction. Reusing a key preserves the first effect."""
        if not kind or ":" in kind:
            raise LedgerError("invalid", "action kind must be nonempty and contain no colon")
        if stable_key is not None:
            budget_stop = (
                kind == SEND
                and cause.source == "core"
                and cause.kind == BUDGET_THRESHOLD
                and cause.fields.get("percent") == 100
                and stable_key == f"{BUDGET_ITEM.name}:{cause.upstream_id}"
                and cause.upstream_id == f"{cause.fields.get('budget_id')}:100"
                and payload.get("lane") == cause.fields.get("lane")
                and payload.get("text") == "stop"
            )
            if self._append_role != "system" or not (
                stable_key.startswith(f"{kind}:") or budget_stop
            ):
                raise LedgerError("forbidden", "only core may supply a stable action key")
        key = stable_key or f"{cause.id}:{kind}:{discriminator}"
        await self._tx.insert_action(Action(key, kind, cause.id, payload))
        return key

    async def update_action(self, action: Action) -> None:
        if self._append_role != "system":
            raise LedgerError("forbidden", "only core may update a committed action")
        await self._tx.update_action(action)

    async def insert_outbox(self, message: OutboxMessage) -> None:
        await self._tx.insert_outbox(message)

    async def insert_decision(self, decision: Decision) -> None:
        await self._tx.insert_decision(decision)
        await self.index_search_record(decision_record(decision))

    async def lease_outbox(self, lease_id: str, now: str, expires_at: str) -> OutboxLease | None:
        return await self._tx.lease_outbox(lease_id, now, expires_at)

    async def complete_outbox(self, lease_id: str, now: str) -> str | None:
        return await self._tx.complete_outbox(lease_id, now)

    async def retire_stale_outbox(self) -> None:
        await self._tx.retire_stale_outbox(format_time(self.now()))

    async def find_trigger(self, trigger_id: str) -> Trigger | None:
        return await self._tx.find_trigger(trigger_id)

    async def open_triggers(self) -> list[Trigger]:
        return await self._tx.open_triggers()

    async def save_trigger(self, trigger: Trigger) -> None:
        await self._tx.save_trigger(trigger)

    async def first_matching_pull(self, repository: str, pattern: str) -> int | None:
        return await self._tx.first_matching_pull(repository, pattern)

    def item_kind(self, kind: str) -> ItemKind:
        if kind not in self._items:
            raise LedgerError("invalid", f"unknown next item kind {kind!r}")
        return self._items[kind]

    async def find_item(self, item_id: str) -> Item | None:
        return await self._tx.find_item(item_id)

    async def save_item(self, item: Item) -> None:
        await self._tx.save_item(item)

    async def raise_item(
        self, kind: str, subject_ref: str, role: str, context: dict[str, Any]
    ) -> Item:
        """Open an occurrence or update the active one when its condition changes."""
        registered = self.item_kind(kind)
        role_family(role)
        key = f"{kind}:{subject_ref}"
        current = await self._tx.active_item(key)
        if current is not None:
            if not registered.state_backed or current.context == context:
                return current
            item = replace(current, state="open", context=context, seq=await self.allocate_seq())
        else:
            seq = await self.allocate_seq()
            item = Item(
                f"item_{seq}",
                kind,
                subject_ref,
                key,
                role,
                registered.rank_class,
                "open",
                seq,
                None,
                seq,
                context,
                registered.suggested_actions,
            )
        await self.save_item(item)
        return item

    async def active_item(self, kind: str, subject_ref: str) -> Item | None:
        return await self._tx.active_item(f"{kind}:{subject_ref}")

    async def active_item_subjects(self, kind: str) -> set[str]:
        return await self._tx.active_item_subjects(kind)

    async def clear_item(self, kind: str, subject_ref: str) -> None:
        if not self.item_kind(kind).state_backed:
            raise LedgerError("invalid", f"{kind!r} is not state-backed")
        if (item := await self._tx.active_item(f"{kind}:{subject_ref}")) is not None:
            seq = await self.allocate_seq()
            await self.save_item(replace(item, state="closed", closed_seq=seq, seq=seq))

    async def append(self, event: NewEvent) -> AppendResult:
        """Validate, deduplicate, number and store ``event``.

        A duplicate of ``(source, kind, upstream_id)`` returns the stored event with
        ``created=False`` and touches nothing: no ``seq`` is consumed. The identity is checked
        first, the duplicate lookup next, and the declared fields only for a new event.
        """
        registered = self._events.validate_identity(self._append_role, event)
        existing = await self._tx.find_event(event.source, event.kind, event.upstream_id)
        if existing is not None:
            return AppendResult(existing, False)
        # One copy is validated and stored: the caller owns event.fields and may change it while
        # this input awaits the store.
        fields = copy.deepcopy(event.fields)
        validate_fields(registered.fields, fields, event.kind)
        stored = Event(
            seq=await self._tx.allocate_seq(),
            id=self._id_factory(),
            source=event.source,
            kind=event.kind,
            upstream_id=event.upstream_id,
            lane_id=event.lane_id,
            fields=fields,
            body=event.body,
            observed_at=format_time(self._clock.now()),
        )
        await self._tx.insert_event(stored)
        if event.kind == "core.tick":
            self._tick = True
        if self._search is not None:
            await self._search.index(self._tx, stored)
        return AppendResult(stored, True)
