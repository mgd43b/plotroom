import asyncio
from pathlib import Path

import pytest

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.actions import Action
from plotroom.core.catalog import build_catalog
from plotroom.core.events import NewEvent
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork


async def test_action_attempts_survive_restart_and_ticks_follow_clock(tmp_path: Path) -> None:
    clock = FixedClock()
    attempts: list[int] = []
    committed_attempts: list[bool] = []

    async def fail(action: Action) -> None:
        attempts.append(action.attempts + 1)
        committed = await ledger.pending_action()
        committed_attempts.append(committed == action)
        raise RuntimeError("fixture failure")

    async def open_runtime() -> tuple[SqliteStore, Ledger, ProjectRuntime]:
        store = SqliteStore("demo", tmp_path)
        await store.migrate()
        opened = Ledger(store, build_catalog(), clock)
        opened.start()
        return (
            store,
            opened,
            ProjectRuntime(
                opened, clock, tick_seconds=5, action_attempts=2, handlers={"fixture": fail}
            ),
        )

    async def enqueue(uow: UnitOfWork) -> str:
        cause = (await uow.append(NewEvent("core", "core.tick", "cause"))).event
        key = await uow.enqueue_action(cause, "fixture", {"try": 1})
        assert await uow.enqueue_action(cause, "fixture", {"try": 2}) == key
        return key

    store, ledger, runtime = await open_runtime()
    try:
        key = await ledger.run("system", enqueue)
        assert (action := await ledger.pending_action()) is not None
        assert action.payload == {"try": 1}
        clock.advance(4)
        assert not await runtime.tick_if_due()
        clock.advance(1)
        assert await runtime.tick_if_due()
        assert not await runtime.tick_if_due()
        clock.advance(5)
        assert await runtime.tick_if_due()
        assert await runtime.run_one_action()
    finally:
        await ledger.stop()
        await store.close()

    store, ledger, runtime = await open_runtime()
    try:
        assert await runtime.run_one_action()
        assert not await runtime.run_one_action()
        assert attempts == [1, 2]
        assert committed_attempts == [True, True]
        _, items, _, _ = await ledger.read_board("orchestrator")
        assert len(items) == 1
        assert items[0].subject_ref == key
        assert items[0].kind == "failed_action"
        assert (await ledger.execute("orchestrator", "ack", "ack", {"item": items[0].id})).result[
            "state"
        ] == "closed"
        assert not (await ledger.read_board("orchestrator"))[1]
        ticks = [
            event.upstream_id for event in await ledger.events_after(0) if event.kind == "core.tick"
        ]
        assert ticks == ["cause", "2026-11-22T12:00:05.000000Z", "2026-11-22T12:00:10.000000Z"]
    finally:
        await ledger.stop()
        await store.close()


async def test_interrupted_final_attempt_can_deliver_after_restart(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    ledger = Ledger(store, build_catalog(), clock)
    ledger.start()
    entered = asyncio.Event()
    delivered: list[str] = []

    async def enqueue(uow: UnitOfWork) -> str:
        cause = (await uow.append(NewEvent("core", "core.tick", "cause"))).event
        return await uow.enqueue_action(cause, "fixture", {})

    async def interrupted(action: Action) -> None:
        entered.set()
        await asyncio.Future[None]()

    async def deliver(action: Action) -> None:
        delivered.append(action.key)

    try:
        key = await ledger.run("system", enqueue)
        runtime = ProjectRuntime(
            ledger,
            clock,
            tick_seconds=5,
            action_attempts=1,
            handlers={"fixture": interrupted},
        )
        dispatch = asyncio.create_task(runtime.run_one_action())
        async with asyncio.timeout(2):
            await entered.wait()
        dispatch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await dispatch
        assert (action := await ledger.pending_action()) is not None
        assert action.attempts == 0
        await ledger.stop()
        ledger.start()
        assert not ledger.commit_signal.is_set()
        resumed = ProjectRuntime(
            ledger,
            clock,
            tick_seconds=5,
            action_attempts=1,
            handlers={"fixture": deliver},
        )
        assert await resumed.run_one_action()
        assert delivered == [key]
        assert await ledger.pending_action() is None
        assert not (await ledger.read_board("orchestrator"))[1]
    finally:
        await ledger.stop()
        await store.close()
