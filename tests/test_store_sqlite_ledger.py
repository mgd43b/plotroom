"""The SQLite ledger store: schema, the writer transaction, reads beside it, restarts."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from fakes import FixedClock, Fixture, apply_inputs, load_inputs, sqlite_dump
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.events import Event, NewEvent
from plotroom.core.ledger import Ledger


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteStore]:
    opened = SqliteStore("demo", tmp_path)
    await opened.migrate()
    try:
        yield opened
    finally:
        await opened.close()


def note(key: str) -> NewEvent:
    return NewEvent("fixture", "fixture.noted", key, {"text": key})


def stored(seq: int, key: str = "k") -> Event:
    return Event(seq, f"evt_{seq}", "fixture", "fixture.noted", key, None, {}, "", "t")


# the schema


async def test_the_schema_enforces_what_the_core_relies_on(store: SqliteStore) -> None:
    insert = "INSERT INTO events VALUES (?, ?, 'fixture', 'fixture.noted', ?, NULL, '{}', '', 't')"
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT id, value FROM project_seq").fetchall() == [(1, 0)]
        db.execute(insert, (1, "evt_1", "a"))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            db.execute(insert, (2, "evt_2", "a"))  # same (source, kind, upstream_id)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            db.execute(insert, (3, "evt_1", "b"))  # same id
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute(insert, (0, "evt_0", "c"))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute("INSERT INTO events VALUES (5, 'e5', 's', 'k', 'u', NULL, 'nope', '', 't')")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute("INSERT INTO project_seq VALUES (2, 0)")  # one counter per project
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO events (seq, id) VALUES (9, 'x')")  # NOT NULL columns


# the writer transaction


async def test_a_committed_transaction_is_visible_and_a_rolled_back_one_leaves_nothing(
    store: SqliteStore,
) -> None:
    async with store.transaction() as tx:
        assert await tx.allocate_seq() == 1
        await tx.insert_event(stored(1, "kept"))

    async def abandoned() -> None:
        async with store.transaction() as tx:
            assert await tx.allocate_seq() == 2
            await tx.insert_event(stored(2, "doomed"))
            assert await tx.find_event("fixture", "fixture.noted", "doomed") is not None
            raise RuntimeError("abandon")

    with pytest.raises(RuntimeError, match="abandon"):
        await abandoned()
    assert await store.head_seq() == 1
    assert [e.upstream_id for e in await store.events_after(0)] == ["kept"]
    async with store.transaction() as tx:  # the writer connection is clean for the next input
        assert await tx.allocate_seq() == 2
        assert await tx.find_event("fixture", "fixture.noted", "doomed") is None


async def test_the_writer_takes_the_write_lock_when_it_begins_and_readers_are_not_blocked(
    store: SqliteStore,
) -> None:
    async with store.transaction() as tx:
        await tx.allocate_seq()
        await tx.insert_event(stored(1))
        other = sqlite3.connect(store.path, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        finally:
            other.close()
        # WAL: other connections read the last committed state meanwhile
        assert await store.head_seq() == 0
        assert await store.events_after(0) == []
        assert sqlite_dump(store.path)["events"] == []
    assert await store.head_seq() == 1
    assert [e.seq for e in await store.events_after(0)] == [1]


async def test_the_write_lock_is_taken_by_begin_not_by_the_first_write(store: SqliteStore) -> None:
    async with store.transaction():
        other = sqlite3.connect(store.path, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        finally:
            other.close()


async def test_a_failed_begin_does_not_wedge_the_writer(store: SqliteStore) -> None:
    writer = await store._writer_connection()  # pyright: ignore[reportPrivateUsage]
    await writer.exec_driver_sql("PRAGMA busy_timeout = 50")
    await writer.commit()  # the pragma autobegan a transaction
    rival = sqlite3.connect(store.path, timeout=0, isolation_level=None)
    rival.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(Exception, match="locked"):
            async with store.transaction():
                pytest.fail("the begin must fail while another connection holds the lock")
    finally:
        rival.execute("ROLLBACK")
        rival.close()
    async with store.transaction() as tx:  # a new writer connection takes over
        assert await tx.allocate_seq() == 1


async def test_canceling_a_task_inside_a_transaction_rolls_it_back_whole(
    store: SqliteStore,
) -> None:
    inside = asyncio.Event()

    async def work() -> None:
        async with store.transaction() as tx:
            await tx.allocate_seq()
            await tx.insert_event(stored(1, "doomed"))
            inside.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(work())
    await inside.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await store.head_seq() == 0
    assert await store.events_after(0) == []
    async with store.transaction() as tx:  # and the store is free again
        assert await tx.allocate_seq() == 1


async def test_closing_a_store_after_use_and_again_is_harmless(store: SqliteStore) -> None:
    async with store.transaction() as tx:
        await tx.allocate_seq()
    await store.close()
    await store.close()


# restart


async def test_events_seq_and_dedupe_persist_across_a_restart(tmp_path: Path) -> None:
    fixture = Fixture()
    first = SqliteStore("demo", tmp_path)
    await first.migrate()
    ledger = Ledger(first, fixture.catalog, FixedClock())
    ledger.start()
    inputs = load_inputs()
    try:
        await apply_inputs(ledger, inputs)
    finally:
        await ledger.stop()
        await first.close()
    before = sqlite_dump(first.path)

    reopened = SqliteStore("demo", tmp_path)
    again = Ledger(reopened, fixture.catalog, FixedClock())
    again.start()
    try:
        assert await again.head_seq() == 7
        assert [e.seq for e in await again.events_after(0)] == [1, 2, 3, 4, 5, 6, 7]
        # the same inputs, replayed against the reopened file, change nothing (dedupe persisted)
        assert await apply_inputs(again, inputs) == [False] * len(inputs)
        assert sqlite_dump(first.path) == before
        # and the counter carries on from where it was
        assert (await again.append("system", note("after-restart"))).event.seq == 8
    finally:
        await again.stop()
        await reopened.close()
