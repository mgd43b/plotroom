"""Test doubles and fixtures for the ledger: a ``MemoryStore``, a ``FixedClock`` and a catalog."""

from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.catalog import Catalog, build_catalog
from plotroom.core.commands import Command, CommandError
from plotroom.core.events import AppendResult, EventKind, FieldSpec, NewEvent
from plotroom.core.events import Event as StoredEvent
from plotroom.core.ledger import Ledger
from plotroom.core.roles import WriterRule
from plotroom.core.unit_of_work import UnitOfWork

FIXTURES = Path(__file__).parent / "fixtures"
START = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


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

    async def insert_event(self, event: StoredEvent) -> None:
        self._live()
        await asyncio.sleep(0)
        if any(
            (e.source, e.kind, e.upstream_id) == (event.source, event.kind, event.upstream_id)
            for e in self.events
        ):
            raise RuntimeError("duplicate event identity reached the store")
        self.events.append(copy.deepcopy(event))

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
        try:
            yield tx
        except BaseException:
            self.rollbacks += 1
            raise
        else:
            self._events, self._seq = tx.events, tx.seq
            self.commits += 1
        finally:
            tx.expired = True
            self._open = False

    async def head_seq(self) -> int:
        return self._seq

    async def events_after(self, seq: int, limit: int = 100) -> list[StoredEvent]:
        return [copy.deepcopy(e) for e in self._events if e.seq > seq][: max(limit, 0)]

    async def close(self) -> None:
        self.closed = True

    def dump(self) -> dict[str, Any]:
        """Every table's content, for comparing states."""
        return {"events": [asdict(e) for e in self._events], "project_seq": self._seq}


class Fixture:
    """A catalog with fixture kinds and commands, and a record of which handlers ran."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.catalog: Catalog = build_catalog()
        events = self.catalog.events
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
