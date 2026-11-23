"""Test doubles and fixtures for the ledger: a ``MemoryStore``, a ``FixedClock`` and a catalog."""

from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from itertools import islice
from pathlib import Path
from typing import Any

from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.actions import BRANCH_UPDATE, CONTROLLER_MERGE, Action, QueuedAction
from plotroom.core.catalog import Catalog
from plotroom.core.checklists import Checklist
from plotroom.core.claims import OVERLAP_KINDS, Claim
from plotroom.core.commands import Command, CommandError, CommandRegistry
from plotroom.core.events import AppendResult, EventKind, EventRegistry, FieldSpec, NewEvent
from plotroom.core.events import Event as StoredEvent
from plotroom.core.github import PULL_REQUESTS, FetchCursor, FetchRetry, GitHubSummary, PullRequest
from plotroom.core.github_projection import GitHubResource
from plotroom.core.lanes import Lane, with_projected_pr
from plotroom.core.ledger import Ledger
from plotroom.core.merge_gate import GateSnapshot, load_gate_snapshot
from plotroom.core.next_items import Item, visible
from plotroom.core.outbox import OutboxLease, OutboxMessage, OutboxReadiness
from plotroom.core.roles import WriterRule
from plotroom.core.triggers import Trigger
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.store import Board
from plotroom.ports.work_items import WorkItem

FIXTURES = Path(__file__).parent / "fixtures"
START = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


def _visible_claims(
    claims: dict[tuple[str, str], Claim],
    lanes: dict[str, Lane],
    role: str,
    lane: str | None = None,
) -> list[Claim]:
    return [
        entry
        for key, entry in sorted(claims.items())
        if (lane is None or key[0] == lane)
        and lanes[key[0]].state != "archived"
        and role in {"orchestrator", "clerk", f"worker:{lanes[key[0]].worker}"}
    ]


def _branch_pull(
    pulls: dict[tuple[str, int], PullRequest], repository: str, branch: str
) -> PullRequest | None:
    matches = [
        pull
        for pull in pulls.values()
        if pull.repository == pull.head_repository == repository
        and pull.branch == branch
        and pull.state == "open"
        and pull.complete
    ]
    return matches[0] if len(matches) == 1 else None


class FixedClock:
    """A ``Clock`` that stands still until told to advance."""

    def __init__(self, now: datetime = START) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class _MemoryTransaction:
    """Works on staged copies; ``MemoryStore.transaction`` publishes them on commit."""

    def __init__(
        self, events: list[StoredEvent], seq: int, on_allocate: Callable[[], None] | None = None
    ) -> None:
        self.events = events
        self.items: dict[str, Item] = {}
        self.changes: list[Item] = []
        self.receipts: dict[tuple[str, str], dict[str, Any]] = {}
        self.actions: dict[str, Action] = {}
        self.outbox: dict[str, OutboxMessage] = {}
        self.leases: dict[str, OutboxLease] = {}
        self.delivered: set[str] = set()
        self.triggers: dict[str, Trigger] = {}
        self.lanes: dict[str, Lane] = {}
        self.checklists: dict[str, Checklist] = {}
        self.claims: dict[tuple[str, str], Claim] = {}
        self.work_items: tuple[WorkItem, ...] = ()
        self.claude_turns: dict[tuple[str, str], set[str]] = {}
        self.pulls: dict[tuple[str, int], PullRequest] = {}
        self.branch_order: dict[tuple[str, int, str], None] = {}
        self.github_resources: dict[tuple[str, str, str], GitHubResource] = {}
        self.fetch_cursors: dict[tuple[str, str], FetchCursor] = {}
        self.fetch_retries: dict[tuple[str, str], FetchRetry] = {}
        self.freeze_reason: str | None = None
        self.seq = seq
        self.expired = False
        self._on_allocate = on_allocate

    def _live(self) -> None:
        if self.expired:
            raise RuntimeError("this transaction has ended; a retained handle cannot write")

    async def find_event(self, source: str, kind: str, upstream_id: str) -> StoredEvent | None:
        self._live()
        await asyncio.sleep(0)
        for event in self.events:
            if (event.source, event.kind, event.upstream_id) == (source, kind, upstream_id):
                return copy.deepcopy(event)
        return None

    async def ready_work_items(self) -> tuple[WorkItem, ...]:
        self._live()
        return self.work_items

    async def find_claude_turn(self, session: str, aliases: tuple[str, ...]) -> StoredEvent | None:
        self._live()
        index = next((alias for alias in aliases if alias.startswith("index:")), None)
        incompatible = {
            event
            for (owner, key), events in self.claude_turns.items()
            if index is not None and owner == session and key.startswith("index:") and key != index
            for event in events
        }
        for alias in aliases:
            matches = self.claude_turns.get((session, alias), set()) - incompatible
            if matches:
                return next(
                    copy.deepcopy(row) for row in reversed(self.events) if row.id in matches
                )
        return None

    async def bind_claude_turn(self, session: str, aliases: tuple[str, ...], event: str) -> None:
        self._live()
        for alias in aliases:
            self.claude_turns.setdefault((session, alias), set()).add(event)

    async def replace_work_items(self, items: tuple[WorkItem, ...]) -> None:
        self._live()
        self.work_items = items

    async def claimed_work_items(self) -> set[str]:
        self._live()
        return {
            ref
            for lane in self.lanes.values()
            if lane.state != "archived"
            for ref in lane.work_items
        }

    async def find_lane(self, lane_id: str) -> Lane | None:
        self._live()
        return self.lanes.get(lane_id)

    async def worker_lane(self, worker: str) -> Lane | None:
        self._live()
        return next(
            (
                lane
                for lane in self.lanes.values()
                if lane.worker == worker and lane.state != "archived"
            ),
            None,
        )

    async def branch_lane(self, branch: str) -> Lane | None:
        lanes = await self.branch_lanes(branch)
        return lanes[0] if len(lanes) == 1 else None

    async def branch_lanes(self, branch: str) -> list[Lane]:
        self._live()
        return sorted(
            (
                lane
                for lane in self.lanes.values()
                if lane.branch == branch and lane.state != "archived"
            ),
            key=lambda lane: lane.id,
        )

    async def active_lanes(self) -> list[Lane]:
        self._live()
        return [lane for lane in self.lanes.values() if lane.state != "archived"]

    async def pull_request(self, repository: str, number: int) -> PullRequest | None:
        self._live()
        return self.pulls.get((repository, number))

    async def lane_claims(self, lane: str | None = None) -> list[Claim]:
        self._live()
        return _visible_claims(self.claims, self.lanes, "orchestrator", lane)

    async def save_claims(self, claims: list[Claim]) -> None:
        self._live()
        self.claims.update({(entry.lane, entry.glob): entry for entry in claims})

    async def delete_claims(self, lane: str, globs: list[str] | None) -> None:
        self._live()
        self.claims = {
            key: entry
            for key, entry in self.claims.items()
            if entry.lane != lane or (globs is not None and entry.glob not in globs)
        }

    async def open_pulls(self, repository: str) -> tuple[PullRequest, ...]:
        self._live()
        return tuple(
            pull
            for _, pull in sorted(self.pulls.items())
            if pull.repository == repository and pull.state == "open" and pull.complete
        )

    async def open_branch_pull(self, repository: str, branch: str) -> PullRequest | None:
        self._live()
        return _branch_pull(self.pulls, repository, branch)

    async def latest_merge_verdict(self, repository: str, number: int) -> StoredEvent | None:
        self._live()
        return next(
            (
                event
                for event in reversed(self.events)
                if event.kind == "core.merge_verdict"
                and event.fields["repository"] == repository
                and event.fields["number"] == number
            ),
            None,
        )

    async def save_merge_verdict(self, repository: str, number: int, seq: int) -> None:
        self._live()

    async def deploy_freeze(self) -> str | None:
        self._live()
        return self.freeze_reason

    async def set_deploy_freeze(self, reason: str | None) -> None:
        self._live()
        self.freeze_reason = reason

    async def save_pull_request(self, pull: PullRequest) -> None:
        self._live()
        self.pulls[pull.repository, pull.number] = pull
        if pull.complete:
            self.branch_order[pull.repository, pull.number, pull.branch] = None

    async def github_resource(
        self, repository: str, resource: str, scope: str = ""
    ) -> GitHubResource | None:
        self._live()
        return copy.deepcopy(self.github_resources.get((repository, resource, scope)))

    async def save_github_resource(
        self, repository: str, resource: str, scope: str, result: GitHubResource
    ) -> None:
        self._live()
        self.github_resources[repository, resource, scope] = copy.deepcopy(result)

    async def fetch_cursor(self, repository: str, resource: str) -> FetchCursor | None:
        self._live()
        return self.fetch_cursors.get((repository, resource))

    async def save_fetch_cursor(self, repository: str, resource: str, cursor: FetchCursor) -> None:
        self._live()
        self.fetch_cursors[repository, resource] = cursor

    async def save_fetch_retry(
        self, repository: str, resource: str, retry: FetchRetry | None
    ) -> None:
        self._live()
        if retry is None:
            self.fetch_retries.pop((repository, resource), None)
        else:
            self.fetch_retries[repository, resource] = retry

    async def save_lane(self, lane: Lane) -> None:
        self._live()
        self.lanes[lane.id] = lane

    async def find_checklist(self, lane: str) -> Checklist | None:
        self._live()
        return self.checklists.get(lane)

    async def save_checklist(self, checklist: Checklist) -> None:
        self._live()
        self.checklists[checklist.lane] = checklist

    async def set_checklist_step_completion(
        self, lane: str, version: int, step: str, completion_ref: str
    ) -> bool:
        self._live()
        checklist = self.checklists.get(lane)
        if checklist is None or checklist.state != "active" or checklist.version != version:
            return False
        steps = list(checklist.steps)
        for index, current in enumerate(steps):
            if current.step == step and current.completion_ref is None:
                steps[index] = replace(current, completion_ref=completion_ref)
                self.checklists[lane] = replace(checklist, steps=tuple(steps))
                return True
        return False

    async def insert_action(self, action: Action) -> None:
        self._live()
        self.actions.setdefault(action.key, copy.deepcopy(action))

    async def update_action(self, action: Action) -> None:
        self._live()
        self.actions[action.key] = copy.deepcopy(action)

    async def insert_outbox(self, message: OutboxMessage) -> None:
        self._live()
        self.outbox.setdefault(message.action_key, message)

    async def lease_outbox(self, lease_id: str, now: str, expires_at: str) -> OutboxLease | None:
        self._live()
        for key, message in self.outbox.items():
            if key not in self.delivered and (
                key not in self.leases or self.leases[key].expires_at <= now
            ):
                lease = OutboxLease(message, lease_id, expires_at)
                self.leases[key] = lease
                return lease
        return None

    async def complete_outbox(self, lease_id: str, now: str) -> str | None:
        self._live()
        for key, lease in self.leases.items():
            if key not in self.delivered and lease.act_id == lease_id and lease.expires_at > now:
                self.delivered.add(key)
                return key
        return None

    async def find_trigger(self, trigger_id: str) -> Trigger | None:
        self._live()
        return copy.deepcopy(self.triggers.get(trigger_id))

    async def open_triggers(self) -> list[Trigger]:
        self._live()
        return [copy.deepcopy(t) for t in self.triggers.values() if t.state == "open"]

    async def save_trigger(self, trigger: Trigger) -> None:
        self._live()
        self.triggers[trigger.id] = copy.deepcopy(trigger)

    async def first_matching_pull(self, repository: str, pattern: str) -> int | None:
        self._live()
        for repo, number, branch in self.branch_order:
            if repo != repository or not fnmatchcase(branch, pattern):
                continue
            pull = self.pulls[repo, number]
            if pull.complete and pull.branch == branch:
                return number
        return None

    async def latest_event(
        self,
        kind: str,
        *,
        fields: Mapping[str, str | bool] | None = None,
        after: int = 0,
    ) -> StoredEvent | None:
        self._live()
        for event in reversed(self.events):
            if (
                event.seq > after
                and event.kind == kind
                and all(event.fields.get(key) == value for key, value in (fields or {}).items())
            ):
                return copy.deepcopy(event)
        return None

    async def latest_run_report(self, run_id: str) -> StoredEvent | None:
        return await self.latest_event("run.status", fields={"run_id": run_id})

    async def lane_activity(self, lane: str) -> StoredEvent | None:
        self._live()
        commands = {
            "core.lane_start",
            "core.lane_update",
            "core.lane_ready",
            "core.lane_archive",
            "core.claim",
            "core.release",
        }
        for event in reversed(self.events):
            if (
                event.lane_id == lane
                and event.kind not in {"core.trigger_fired", "core.lane_marked"}
            ) or (event.kind in commands and event.fields["args"]["lane"] == lane):
                return copy.deepcopy(event)
        return None

    async def insert_event(self, event: StoredEvent) -> None:
        self._live()
        await asyncio.sleep(0)
        if any(
            (e.source, e.kind, e.upstream_id) == (event.source, event.kind, event.upstream_id)
            for e in self.events
        ):
            raise RuntimeError("duplicate event identity reached the store")
        self.events.append(copy.deepcopy(event))

    async def find_noop_receipt(self, command: str, key: str) -> dict[str, Any] | None:
        self._live()
        return copy.deepcopy(self.receipts.get((command, key)))

    async def insert_noop_receipt(self, command: str, key: str, receipt: dict[str, Any]) -> None:
        self._live()
        self.receipts[command, key] = copy.deepcopy(receipt)

    async def active_item_subjects(self, kind: str) -> set[str]:
        self._live()
        return {
            item.subject_ref
            for item in self.items.values()
            if item.kind == kind and item.state != "closed"
        }

    async def find_item(self, item_id: str) -> Item | None:
        self._live()
        return copy.deepcopy(self.items.get(item_id))

    async def active_item(self, key: str) -> Item | None:
        self._live()
        return copy.deepcopy(
            next(
                (i for i in self.items.values() if i.dedup_key == key and i.state != "closed"), None
            )
        )

    async def save_item(self, item: Item) -> None:
        self._live()
        self.items[item.id] = copy.deepcopy(item)
        self.changes.append(copy.deepcopy(item))

    async def allocate_seq(self) -> int:
        self._live()
        await asyncio.sleep(0)
        if self._on_allocate is not None:
            self._on_allocate()  # a hook standing in for another task running during the await
        self.seq += 1
        return self.seq


class MemoryStore:
    """A ``Store`` in memory that honors the port: all-or-nothing transactions, one at a time."""

    def __init__(self, project_id: str = "demo") -> None:
        self._project_id = project_id
        self._events: list[StoredEvent] = []
        self._seq = 0
        self._items: dict[str, Item] = {}
        self._changes: list[Item] = []
        self._receipts: dict[tuple[str, str], dict[str, Any]] = {}
        self._actions: dict[str, Action] = {}
        self._outbox: dict[str, OutboxMessage] = {}
        self._leases: dict[str, OutboxLease] = {}
        self._delivered: set[str] = set()
        self._triggers: dict[str, Trigger] = {}
        self._lanes: dict[str, Lane] = {}
        self._checklists: dict[str, Checklist] = {}
        self._claims: dict[tuple[str, str], Claim] = {}
        self._work_items: tuple[WorkItem, ...] = ()
        self._claude_turns: dict[tuple[str, str], set[str]] = {}
        self._pulls: dict[tuple[str, int], PullRequest] = {}
        self._branch_order: dict[tuple[str, int, str], None] = {}
        self._github_resources: dict[tuple[str, str, str], GitHubResource] = {}
        self._fetch_cursors: dict[tuple[str, str], FetchCursor] = {}
        self._fetch_retries: dict[tuple[str, str], FetchRetry] = {}
        self._freeze_reason: str | None = None
        self._open = False
        self.commits = 0
        self.on_allocate: Callable[[], None] | None = None
        self.rollbacks = 0
        self.closed = False

    @property
    def project_id(self) -> str:
        return self._project_id

    async def revision(self) -> str | None:
        return "memory"

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[_MemoryTransaction]:
        if self._open:
            raise RuntimeError("a writer transaction is already open on this store")
        self._open = True
        tx = _MemoryTransaction(copy.deepcopy(self._events), self._seq, self.on_allocate)
        tx.items = copy.deepcopy(self._items)
        tx.changes = self._changes.copy()
        tx.receipts = copy.deepcopy(self._receipts)
        tx.actions = copy.deepcopy(self._actions)
        tx.outbox = self._outbox.copy()
        tx.leases = self._leases.copy()
        tx.delivered = self._delivered.copy()
        tx.triggers = copy.deepcopy(self._triggers)
        tx.lanes = self._lanes.copy()
        tx.checklists = self._checklists.copy()
        tx.claims = self._claims.copy()
        tx.work_items = self._work_items
        tx.claude_turns = {key: ids.copy() for key, ids in self._claude_turns.items()}
        tx.pulls = self._pulls.copy()
        tx.branch_order = self._branch_order.copy()
        tx.github_resources = copy.deepcopy(self._github_resources)
        tx.fetch_cursors = self._fetch_cursors.copy()
        tx.fetch_retries = self._fetch_retries.copy()
        tx.freeze_reason = self._freeze_reason
        try:
            yield tx
        except BaseException:
            self.rollbacks += 1
            raise
        else:
            self._events, self._seq, self._items = tx.events, tx.seq, tx.items
            self._changes = tx.changes
            self._receipts = tx.receipts
            self._actions = tx.actions
            self._outbox, self._leases, self._delivered = tx.outbox, tx.leases, tx.delivered
            self._triggers = tx.triggers
            self._lanes = tx.lanes
            self._checklists = tx.checklists
            self._claims = tx.claims
            self._work_items = tx.work_items
            self._claude_turns = tx.claude_turns
            self._pulls = tx.pulls
            self._branch_order = tx.branch_order
            self._github_resources = tx.github_resources
            self._fetch_cursors = tx.fetch_cursors
            self._fetch_retries = tx.fetch_retries
            self._freeze_reason = tx.freeze_reason
            self.commits += 1
        finally:
            tx.expired = True
            self._open = False

    async def head_seq(self) -> int:
        return self._seq

    async def outbox_readiness(self, now: str) -> OutboxReadiness:
        ready: list[int] = []
        expiries: list[str] = []
        for key, message in self._outbox.items():
            if key in self._delivered:
                continue
            lease = self._leases.get(key)
            if lease is None or lease.expires_at <= now:
                ready.append(message.seq)
            else:
                expiries.append(lease.expires_at)
        return OutboxReadiness(self._seq, min(ready, default=None), min(expiries, default=None))

    async def events_after(self, seq: int, limit: int = 100) -> list[StoredEvent]:
        return [copy.deepcopy(e) for e in self._events if e.seq > seq][: max(limit, 0)]

    async def read_board(self, role: str) -> tuple[int, list[Item], dict[str, int], list[Lane]]:
        items = [
            copy.deepcopy(i) for i in self._items.values() if i.state == "open" and visible(i, role)
        ]
        lanes = [
            lane
            for lane in self._lanes.values()
            if role in {"orchestrator", "clerk", f"worker:{lane.worker}"}
        ]
        counts = dict(Counter(lane.state for lane in lanes))
        current = sorted(
            (lane for lane in lanes if lane.state != "archived"), key=lambda lane: lane.id
        )
        return self._seq, sorted(items, key=lambda i: (i.rank_class, i.opened_seq)), counts, current

    async def ready_work_items(self) -> tuple[WorkItem, ...]:
        return self._work_items

    async def read_snapshot(
        self, role: str, repository: str | None, *, checklist_lane: str | None = None
    ) -> tuple[Board, GitHubSummary | None, dict[str, int], int, str | None]:
        board, github = (
            (await self.read_board(role), None)
            if repository is None
            else await self.read_github_board(role, repository)
        )
        if checklist_lane is not None:
            _, _, _, lanes = board
            for index, lane in enumerate(lanes):
                if lane.id == checklist_lane:
                    lanes[index] = lane.with_checklist(self._checklists.get(checklist_lane))
                    break
        trigger_counts = dict(Counter(t.state for t in self._triggers.values()))
        claim_count = len(_visible_claims(self._claims, self._lanes, role))
        return board, github, trigger_counts, claim_count, self._freeze_reason

    async def read_github_board(self, role: str, repository: str) -> tuple[Board, GitHubSummary]:
        opened = sum(
            pull.state == "open" for pull in self._pulls.values() if pull.repository == repository
        )
        cursor = await self.fetch_cursor(repository, PULL_REQUESTS)
        seq, items, counts, lanes = await self.read_board(role)
        projected: list[Lane] = []
        for lane in lanes:
            if lane.state == "ready" and (checklist := self._checklists.get(lane.id)) is not None:
                pr = self._pulls.get((repository, checklist.pr))
                if (
                    checklist.state == "pending"
                    and pr is not None
                    and pr.state == "open"
                    and checklist.matches(pr)
                ):
                    projected.append(with_projected_pr(lane, pr.number, pr.head_sha))
                    continue
            pr = _branch_pull(self._pulls, repository, lane.branch)
            projected.append(
                with_projected_pr(lane, pr.number, pr.head_sha)
                if pr is not None
                else with_projected_pr(lane, None, None)
            )
        return (seq, items, counts, projected), GitHubSummary(
            opened, cursor.as_of if cursor else None
        )

    async def read_claims(
        self, role: str, lane: str | None, offset: int, limit: int
    ) -> tuple[int, list[Claim]]:
        claims = _visible_claims(self._claims, self._lanes, role, lane)
        return self._seq, claims[offset : offset + limit]

    async def read_brief_claims(self, worker: str) -> tuple[str | None, list[Claim]]:
        lane = next(
            (
                lane.id
                for lane in self._lanes.values()
                if lane.worker == worker and lane.state != "archived"
            ),
            None,
        )
        return lane, _visible_claims(self._claims, self._lanes, "orchestrator")

    async def read_overlaps(
        self, role: str, lane: str | None, pr: int | None, offset: int, limit: int
    ) -> tuple[int, list[Item]]:
        cursor, items, _, _ = await self.read_board(role)
        path = "lanes" if lane is not None else "prs"
        subject = lane if lane is not None else pr
        overlaps = [
            item for item in items if item.kind in OVERLAP_KINDS and subject in item.context[path]
        ]
        return cursor, overlaps[offset : offset + limit]

    async def list_triggers(self, lane: str | None) -> list[Trigger]:
        return [copy.deepcopy(t) for t in self._triggers.values() if lane is None or t.lane == lane]

    async def read_merge_gate(self, repository: str | None, number: int) -> GateSnapshot:
        reader = _MemoryTransaction(self._events, self._seq)
        reader.lanes = self._lanes
        reader.pulls = self._pulls
        reader.github_resources = self._github_resources
        reader.fetch_cursors = self._fetch_cursors
        reader.freeze_reason = self._freeze_reason
        return await load_gate_snapshot(reader, repository, number)

    async def pull_request(self, repository: str, number: int) -> PullRequest | None:
        return self._pulls.get((repository, number))

    async def github_resource(
        self, repository: str, resource: str, scope: str = ""
    ) -> GitHubResource | None:
        return copy.deepcopy(self._github_resources.get((repository, resource, scope)))

    async def github_pull_targets(self, repository: str) -> tuple[PullRequest, ...]:
        rows = [
            row for row in self._pulls.values() if row.repository == repository and row.complete
        ]
        rows.sort(key=lambda row: (row.state == "open", row.updated_at, row.number), reverse=True)
        return tuple(rows[:100])

    async def fetch_cursor(self, repository: str, resource: str) -> FetchCursor | None:
        return self._fetch_cursors.get((repository, resource))

    async def fetch_retry(self, repository: str, resource: str) -> FetchRetry | None:
        return self._fetch_retries.get((repository, resource))

    async def read_next(self, role: str, offset: int, limit: int) -> tuple[int, list[Item]]:
        _, items, _, _ = await self.read_board(role)
        return self._seq, items[offset : offset + limit]

    async def read_open_item(self, role: str, item_id: str) -> Item | None:
        item = self._items.get(item_id)
        if item is None or item.state != "open" or not visible(item, role):
            return None
        return copy.deepcopy(item)

    async def first_item_change(self, role: str, cursor: int) -> tuple[int, dict[str, Any] | None]:
        found = next((i for i in self._changes if i.seq > cursor and visible(i, role)), None)
        return self._seq, asdict(found) if found is not None else None

    async def pending_action(self) -> Action | None:
        page = await self.pending_actions(after=0, limit=1)
        return page[0].action if page else None

    async def pending_actions(
        self,
        *,
        after: int,
        limit: int,
        skip_branch_updates: bool = False,
        skip_controller_merges: bool = False,
    ) -> tuple[QueuedAction, ...]:
        candidates = (
            QueuedAction(position, action)
            for position, action in enumerate(self._actions.values(), start=1)
            if position > after
            and action.state == "pending"
            and (not skip_branch_updates or action.kind != BRANCH_UPDATE)
            and (not skip_controller_merges or action.kind != CONTROLLER_MERGE)
        )
        return copy.deepcopy(tuple(islice(candidates, limit)))

    async def close(self) -> None:
        self.closed = True

    def dump(self) -> dict[str, Any]:
        """Every table's content, for comparing states."""
        return {"events": [asdict(e) for e in self._events], "project_seq": self._seq}


class Fixture:
    """A catalog with fixture kinds and commands, and a record of which handlers ran."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        events = EventRegistry()
        self.catalog = Catalog(events, CommandRegistry(events))
        events.register(
            EventKind(
                "github.pr_merged",
                (FieldSpec("pr", "int"), FieldSpec("merged_by", "str", required=False)),
                frozenset({"system", "bridge"}),
            )
        )
        events.register(
            EventKind(
                "claude.turn_end",
                (
                    FieldSpec("turns", "int"),
                    FieldSpec("ratio", "number", required=False),
                    FieldSpec("done", "bool", required=False),
                    FieldSpec("meta", "object", required=False),
                    FieldSpec("tags", "array", required=False),
                ),
                frozenset({"worker", "orchestrator"}),
            )
        )
        events.register(
            EventKind("fixture.noted", (FieldSpec("text", "str"),), frozenset({"system"}))
        )
        self.catalog.commands.register(
            Command("record_note", (FieldSpec("key", "str"), FieldSpec("text", "str")), self._note),
            rules=(WriterRule("orchestrator"), WriterRule("clerk"), WriterRule("worker")),
        )
        self.catalog.commands.register(
            Command("explode", (FieldSpec("mode", "str"),), self._explode),
            rules=(WriterRule("orchestrator"), WriterRule("clerk")),
        )

    async def _note(self, uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(args["key"])
        appended = await uow.append(
            NewEvent("fixture", "fixture.noted", args["key"], {"text": args["text"]})
        )
        return {"key": args["key"], "seq": appended.event.seq, "by": uow.role}

    async def _explode(self, uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("explode")
        await uow.append(NewEvent("fixture", "fixture.noted", "doomed", {"text": "never kept"}))
        if args["mode"] == "command_error":
            raise CommandError("conflict", "refused by the handler")
        raise RuntimeError("the handler broke")


def memory_ledger(fixture: Fixture | None = None, clock: FixedClock | None = None) -> Ledger:
    """A started ledger over a ``MemoryStore``; the caller stops it."""
    fixture = fixture or Fixture()
    ledger = Ledger(MemoryStore(), fixture.catalog, clock or FixedClock())
    ledger.start()
    return ledger


def load_inputs() -> list[dict[str, Any]]:
    """The recorded input set: events and commands, in order."""
    inputs: list[dict[str, Any]] = json.loads(
        (FIXTURES / "event_inputs.json").read_text(encoding="utf-8")
    )
    return inputs


async def apply_inputs(ledger: Ledger, inputs: list[dict[str, Any]]) -> list[bool]:
    """Apply recorded inputs in order. ``True`` for each that changed the ledger."""
    changed: list[bool] = []
    for item in inputs:
        if item["input"] == "event":
            appended: AppendResult = await ledger.append(
                item["role"],
                NewEvent(
                    item["source"],
                    item["kind"],
                    item["upstream_id"],
                    item["fields"],
                    item.get("body", ""),
                    item.get("lane_id"),
                ),
            )
            changed.append(appended.created)
        else:
            executed = await ledger.execute(
                item["role"], item["command"], item["request_id"], item["args"]
            )
            changed.append(not executed.replayed)
    return changed


def sqlite_dump(path: Path) -> dict[str, Any]:
    """Every table of a project file, read with plain ``sqlite3`` (WAL lets this run beside a writer)."""

    def identifier(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    db = sqlite3.connect(path)
    try:
        dump: dict[str, Any] = {}
        tables = db.execute(
            "SELECT name, wr FROM pragma_table_list WHERE schema = 'main' "
            "AND type != 'view' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        for name, without_rowid in tables:
            order = "rowid"
            if without_rowid:
                keys = db.execute(
                    "SELECT name FROM pragma_table_info(?) WHERE pk > 0 ORDER BY pk", (name,)
                )
                order = ", ".join(identifier(key) for (key,) in keys)
            dump[name] = db.execute(f"SELECT * FROM {identifier(name)} ORDER BY {order}").fetchall()
        return dump
    finally:
        db.close()


@dataclass
class Backend:
    """A store of either kind and a way to dump everything in it."""

    store: MemoryStore | SqliteStore

    def dump(self) -> dict[str, Any]:
        if isinstance(self.store, MemoryStore):
            return self.store.dump()
        return sqlite_dump(self.store.path)


async def open_backend(name: str, directory: Path) -> Backend:
    """``memory`` or ``sqlite`` (migrated, in ``directory``)."""
    if name == "memory":
        return Backend(MemoryStore())
    store = SqliteStore("demo", directory)
    await store.migrate()
    return Backend(store)
