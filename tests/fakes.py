"""Test doubles and fixtures for the ledger: a ``MemoryStore``, a ``FixedClock`` and a catalog."""

from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from collections import Counter
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.actions import Action
from plotroom.core.catalog import Catalog
from plotroom.core.commands import Command, CommandError, CommandRegistry
from plotroom.core.events import AppendResult, EventKind, EventRegistry, FieldSpec, NewEvent
from plotroom.core.events import Event as StoredEvent
from plotroom.core.github import PULL_REQUESTS, FetchCursor, FetchRetry, GitHubSummary, PullRequest
from plotroom.core.github_projection import GitHubResource
from plotroom.core.lanes import Lane, with_projected_pr
from plotroom.core.ledger import Ledger
from plotroom.core.next_items import Item, visible
from plotroom.core.roles import WriterRule
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.store import Board
from plotroom.ports.work_items import WorkItem

FIXTURES = Path(__file__).parent / "fixtures"
START = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


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
        self.lanes: dict[str, Lane] = {}
        self.work_items: tuple[WorkItem, ...] = ()
        self.claude_turns: dict[tuple[str, str], set[str]] = {}
        self.pulls: dict[tuple[str, int], PullRequest] = {}
        self.github_resources: dict[tuple[str, str, str], GitHubResource] = {}
        self.fetch_cursors: dict[tuple[str, str], FetchCursor] = {}
        self.fetch_retries: dict[tuple[str, str], FetchRetry] = {}
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
        self._live()
        lanes = [
            lane
            for lane in self.lanes.values()
            if lane.branch == branch and lane.state != "archived"
        ]
        return lanes[0] if len(lanes) == 1 else None

    async def pull_request(self, repository: str, number: int) -> PullRequest | None:
        self._live()
        return self.pulls.get((repository, number))

    async def open_branch_pull(self, repository: str, branch: str) -> PullRequest | None:
        self._live()
        return _branch_pull(self.pulls, repository, branch)

    async def save_pull_request(self, pull: PullRequest) -> None:
        self._live()
        self.pulls[pull.repository, pull.number] = pull

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

    async def insert_action(self, action: Action) -> None:
        self._live()
        self.actions.setdefault(action.key, copy.deepcopy(action))

    async def update_action(self, action: Action) -> None:
        self._live()
        self.actions[action.key] = copy.deepcopy(action)

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
        self._lanes: dict[str, Lane] = {}
        self._work_items: tuple[WorkItem, ...] = ()
        self._claude_turns: dict[tuple[str, str], set[str]] = {}
        self._pulls: dict[tuple[str, int], PullRequest] = {}
        self._github_resources: dict[tuple[str, str, str], GitHubResource] = {}
        self._fetch_cursors: dict[tuple[str, str], FetchCursor] = {}
        self._fetch_retries: dict[tuple[str, str], FetchRetry] = {}
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
        tx.lanes = self._lanes.copy()
        tx.work_items = self._work_items
        tx.claude_turns = {key: ids.copy() for key, ids in self._claude_turns.items()}
        tx.pulls = self._pulls.copy()
        tx.github_resources = copy.deepcopy(self._github_resources)
        tx.fetch_cursors = self._fetch_cursors.copy()
        tx.fetch_retries = self._fetch_retries.copy()
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
            self._lanes = tx.lanes
            self._work_items = tx.work_items
            self._claude_turns = tx.claude_turns
            self._pulls = tx.pulls
            self._github_resources = tx.github_resources
            self._fetch_cursors = tx.fetch_cursors
            self._fetch_retries = tx.fetch_retries
            self.commits += 1
        finally:
            tx.expired = True
            self._open = False

    async def head_seq(self) -> int:
        return self._seq

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

    async def read_github_board(self, role: str, repository: str) -> tuple[Board, GitHubSummary]:
        opened = sum(
            pull.state == "open" for pull in self._pulls.values() if pull.repository == repository
        )
        cursor = await self.fetch_cursor(repository, PULL_REQUESTS)
        seq, items, counts, lanes = await self.read_board(role)
        projected = [
            with_projected_pr(lane, pr.number, pr.head_sha)
            if (pr := _branch_pull(self._pulls, repository, lane.branch)) is not None
            else with_projected_pr(lane, None, None)
            for lane in lanes
        ]
        return (seq, items, counts, projected), GitHubSummary(
            opened, cursor.as_of if cursor else None
        )

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
        return copy.deepcopy(
            next((a for a in self._actions.values() if a.state == "pending"), None)
        )

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
    db = sqlite3.connect(path)
    try:
        names = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        return {
            name: db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in names
        }
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
