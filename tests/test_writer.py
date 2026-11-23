"""The per-project writer: one transaction per input, in arrival order, surviving failures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from fakes import Backend, FixedClock, Fixture, MemoryStore, open_backend
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.events import LedgerError, NewEvent
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.core.writer import ProjectWriter

BACKENDS = ["memory", "sqlite"]


@pytest.fixture(params=BACKENDS)
async def backend(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Backend]:
    opened = await open_backend(request.param, tmp_path)
    try:
        yield opened
    finally:
        await opened.store.close()


@pytest.fixture
async def ledger(backend: Backend) -> AsyncIterator[Ledger]:
    started = Ledger(backend.store, Fixture().catalog, FixedClock())
    started.start()
    try:
        yield started
    finally:
        await started.stop()


def note(key: str) -> NewEvent:
    return NewEvent("fixture", "fixture.noted", key, {"text": key})


async def spin() -> None:
    """Let every ready task run."""
    for _ in range(10):
        await asyncio.sleep(0)


async def test_concurrent_inputs_commit_in_arrival_order_with_contiguous_seq(
    ledger: Ledger,
) -> None:
    keys = [f"k{i:02d}" for i in range(25)]
    results = await asyncio.gather(*(ledger.append("system", note(key)) for key in keys))
    assert [r.event.seq for r in results] == list(range(1, 26))
    assert [e.upstream_id for e in await ledger.events_after(0, 100)] == keys
    assert await ledger.head_seq() == 25


async def test_each_input_has_its_own_transaction() -> None:
    store = MemoryStore()
    ledger = Ledger(store, Fixture().catalog, FixedClock())
    ledger.start()
    try:
        await asyncio.gather(*(ledger.append("system", note(f"k{i}")) for i in range(5)))
        with pytest.raises(LedgerError):  # refused before queueing: no transaction at all
            await ledger.append("system", NewEvent("fixture", "fixture.unknown", "x", {}))
        await ledger.append("system", note("k0"))  # a duplicate is still one transaction
    finally:
        await ledger.stop()
    assert (store.commits, store.rollbacks) == (6, 0)


async def test_the_writer_keeps_serving_after_an_input_fails(ledger: Ledger) -> None:
    await ledger.append("system", note("a"))

    async def fails(uow: UnitOfWork) -> None:
        await uow.append(note("doomed"))
        raise ValueError("this input fails")

    with pytest.raises(ValueError, match="this input fails"):
        await ledger.run("system", fails)
    assert (await ledger.append("system", note("b"))).event.seq == 2
    assert [e.upstream_id for e in await ledger.events_after(0)] == ["a", "b"]


async def test_a_caller_canceled_while_queued_is_skipped(ledger: Ledger) -> None:
    release, running = asyncio.Event(), asyncio.Event()
    ran: list[str] = []

    async def hold(uow: UnitOfWork) -> None:
        running.set()
        await release.wait()

    async def second(uow: UnitOfWork) -> None:
        ran.append("second")
        await uow.append(note("second"))

    first = asyncio.create_task(ledger.run("system", hold))
    await running.wait()
    queued = asyncio.create_task(ledger.run("system", second))
    after = asyncio.create_task(ledger.append("system", note("after")))
    await spin()
    queued.cancel()
    await spin()
    release.set()
    await first
    assert (await after).event.seq == 1  # the skipped input took no seq
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert ran == []
    assert [e.upstream_id for e in await ledger.events_after(0)] == ["after"]


async def test_a_caller_canceled_while_its_transaction_runs_does_not_interrupt_it(
    ledger: Ledger,
) -> None:
    release, running = asyncio.Event(), asyncio.Event()

    async def slow(uow: UnitOfWork) -> str:
        await uow.append(note("kept"))
        running.set()
        await release.wait()
        await uow.append(note("also kept"))
        return "done"

    caller = asyncio.create_task(ledger.run("system", slow))
    await running.wait()
    caller.cancel()
    await spin()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    # the transaction still committed whole, and the writer is free for the next input
    assert (await ledger.append("system", note("next"))).event.seq == 3
    assert [e.upstream_id for e in await ledger.events_after(0)] == ["kept", "also kept", "next"]


async def test_a_caller_canceled_mid_transaction_that_then_fails_rolls_back_whole(
    ledger: Ledger,
) -> None:
    release, running = asyncio.Event(), asyncio.Event()

    async def slow_then_fail(uow: UnitOfWork) -> None:
        await uow.append(note("doomed"))
        running.set()
        await release.wait()
        raise RuntimeError("fails after the caller left")

    caller = asyncio.create_task(ledger.run("system", slow_then_fail))
    await running.wait()
    caller.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert (await ledger.append("system", note("next"))).event.seq == 1
    assert [e.upstream_id for e in await ledger.events_after(0)] == ["next"]


async def test_stop_drains_the_queued_inputs_and_later_submits_are_unavailable(
    ledger: Ledger,
) -> None:
    release, running = asyncio.Event(), asyncio.Event()

    async def hold(uow: UnitOfWork) -> None:
        running.set()
        await release.wait()
        await uow.append(note("held"))

    first = asyncio.create_task(ledger.run("system", hold))
    await running.wait()
    queued = [asyncio.create_task(ledger.append("system", note(f"q{i}"))) for i in range(3)]
    await spin()
    stopping = asyncio.create_task(ledger.stop())
    await spin()
    assert not stopping.done()  # still draining
    with pytest.raises(LedgerError) as raised:  # and nothing new is let in
        await ledger.append("system", note("late"))
    assert raised.value.code == "unavailable"
    release.set()
    await stopping
    await first
    assert [(await task).event.seq for task in queued] == [2, 3, 4]
    with pytest.raises(LedgerError) as after:
        await ledger.append("system", note("later"))
    assert after.value.code == "unavailable"
    await ledger.stop()  # stopping twice is harmless
    assert [e.upstream_id for e in await ledger.events_after(0)] == ["held", "q0", "q1", "q2"]


async def test_canceling_stop_does_not_abandon_the_draining(ledger: Ledger) -> None:
    release, running = asyncio.Event(), asyncio.Event()

    async def hold(uow: UnitOfWork) -> None:
        running.set()
        await release.wait()
        await uow.append(note("held"))

    first = asyncio.create_task(ledger.run("system", hold))
    await running.wait()
    stopping = asyncio.create_task(ledger.stop())
    await spin()
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    release.set()
    await first
    await ledger.stop()
    assert [e.upstream_id for e in await ledger.events_after(0)] == ["held"]


async def test_a_writer_that_never_started_refuses_inputs(backend: Backend) -> None:
    ledger = Ledger(backend.store, Fixture().catalog, FixedClock())
    with pytest.raises(LedgerError) as raised:
        await ledger.append("system", note("x"))
    assert raised.value.code == "unavailable"
    await ledger.stop()  # nothing to stop


async def test_a_stopped_writer_can_be_started_again(backend: Backend) -> None:
    ledger = Ledger(backend.store, Fixture().catalog, FixedClock())
    ledger.start()
    assert (await ledger.append("system", note("a"))).event.seq == 1
    await ledger.stop()
    ledger.start()
    try:
        assert (await ledger.append("system", note("b"))).event.seq == 2
        with pytest.raises(RuntimeError, match="already running"):
            ledger.start()
    finally:
        await ledger.stop()


async def test_a_second_concurrent_transaction_on_a_store_raises(backend: Backend) -> None:
    store = backend.store
    async with store.transaction():
        with pytest.raises(RuntimeError, match="already open"):
            async with store.transaction():
                pytest.fail("a second transaction must not open")
    async with store.transaction() as tx:  # and the store is usable again
        assert await tx.allocate_seq() == 1


async def test_a_canceled_writer_task_fails_whatever_is_still_queued(backend: Backend) -> None:
    writer = ProjectWriter(backend.store)
    writer.start()
    release, running = asyncio.Event(), asyncio.Event()

    async def hold(tx: object) -> None:
        running.set()
        await release.wait()

    first = asyncio.create_task(writer.submit(hold))
    await running.wait()
    queued = asyncio.create_task(writer.submit(hold))
    await spin()
    task = writer._task  # pyright: ignore[reportPrivateUsage]
    assert task is not None
    task.cancel()
    for waiting in (first, queued):
        with pytest.raises(LedgerError) as raised:
            await waiting
        assert raised.value.code == "unavailable"
    assert writer.running is False
    if isinstance(backend.store, SqliteStore):
        assert await backend.store.head_seq() == 0


async def test_work_that_submits_to_its_own_writer_fails_fast_and_rolls_back(
    ledger: Ledger,
) -> None:
    async def reenters(uow: UnitOfWork) -> None:
        await uow.append(note("doomed"))
        await ledger.append("system", note("inner"))  # would wait behind itself

    with pytest.raises(RuntimeError, match="re-entrant submit: use the UnitOfWork inside work"):
        await asyncio.wait_for(ledger.run("system", reenters), timeout=10)
    assert await ledger.head_seq() == 0
    assert (await ledger.append("system", note("next"))).event.seq == 1  # the writer serves on
    assert [e.upstream_id for e in await ledger.events_after(0)] == ["next"]


async def test_stop_after_the_writer_task_ended_canceled_returns_normally(
    backend: Backend,
) -> None:
    writer = ProjectWriter(backend.store)
    writer.start()
    task = writer._task  # pyright: ignore[reportPrivateUsage]
    assert task is not None
    await spin()
    task.cancel()
    await spin()
    assert task.cancelled()
    await writer.stop()
    await writer.stop()


async def test_events_after_treats_a_limit_below_one_as_empty(ledger: Ledger) -> None:
    for key in ("a", "b", "c"):
        await ledger.append("system", note(key))
    assert [e.seq for e in await ledger.events_after(0, 2)] == [1, 2]
    for limit in (1, 0, -1, -5):
        assert len(await ledger.events_after(0, limit)) == max(limit, 0)


async def test_a_retained_unit_of_work_cannot_write_after_its_transaction(ledger: Ledger) -> None:
    async def keep(uow: UnitOfWork) -> UnitOfWork:
        return uow

    kept = await ledger.run("system", keep)
    with pytest.raises(RuntimeError, match="transaction has ended"):
        await kept.append(note("late"))
    with pytest.raises(RuntimeError, match="transaction has ended"):
        await kept.allocate_seq()
    assert await ledger.head_seq() == 0
    assert (await ledger.append("system", note("next"))).event.seq == 1


async def test_work_that_stops_its_own_writer_fails_fast_and_the_writer_serves_on(
    ledger: Ledger,
) -> None:
    async def stops(uow: UnitOfWork) -> None:
        await uow.append(note("doomed"))
        await ledger.stop()  # would wait for itself

    with pytest.raises(RuntimeError, match="re-entrant stop"):
        await asyncio.wait_for(ledger.run("system", stops), timeout=10)
    assert await ledger.head_seq() == 0
    assert (await ledger.append("system", note("next"))).event.seq == 1


async def test_work_that_awaits_a_canceled_child_fails_alone_and_the_writer_serves_on(
    ledger: Ledger,
) -> None:
    async def awaits_canceled_child(uow: UnitOfWork) -> None:
        await uow.append(note("doomed"))
        child = asyncio.ensure_future(asyncio.sleep(3600))
        child.cancel()
        await child  # raises CancelledError although this task was not canceled

    queued = asyncio.create_task(ledger.append("system", note("queued")))
    with pytest.raises(LedgerError) as raised:
        await ledger.run("system", awaits_canceled_child)
    assert raised.value.code == "unavailable"
    assert (await queued).event.seq == 1  # the doomed append rolled back, the queue was served
    assert [e.upstream_id for e in await ledger.events_after(0)] == ["queued"]
    assert (await ledger.append("system", note("after"))).event.seq == 2
