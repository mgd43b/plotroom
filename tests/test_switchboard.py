import asyncio
import json
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.switchboard import Switchboard
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.actions import SWITCHBOARD_SEND, Action
from plotroom.core.catalog import build_catalog
from plotroom.core.events import NewEvent
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork


@asynccontextmanager
async def connected(directory: Path) -> AsyncGenerator[tuple[Ledger, ProjectRuntime]]:
    store = SqliteStore("demo", directory)
    await store.migrate()
    ledger = Ledger(store, build_catalog(frozenset({"fixture"})), FixedClock())
    ledger.start()
    relay = Switchboard(
        ledger,
        "fixture-board",
        (sys.executable, str(Path(__file__).with_name("switchboard_child.py")), str(directory)),
        {"fixture-peer": "fixture"},
    )
    task = asyncio.create_task(relay.run())
    runtime = ProjectRuntime(
        ledger,
        FixedClock(),
        tick_seconds=60,
        action_attempts=1,
        handlers={SWITCHBOARD_SEND: relay.send},
    )
    try:
        yield ledger, runtime
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await ledger.stop()
        await store.close()


async def marker(directory: Path, name: str) -> None:
    async with asyncio.timeout(8):
        while not (directory / name).exists():  # noqa: ASYNC110 - observed across subprocesses
            await asyncio.sleep(0.02)


async def test_send_times_out_when_relay_has_not_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("plotroom.adapters.switchboard.REGISTRATION_WAIT_SECONDS", 0)
    store = SqliteStore("demo", tmp_path)
    relay = Switchboard(
        Ledger(store, build_catalog(), FixedClock()), "fixture", (sys.executable,), {}
    )
    action = Action(
        "evt_fixture:send:", SWITCHBOARD_SEND, "evt_fixture", {"to": "peer", "body": "send"}
    )
    try:
        with pytest.raises(TimeoutError):
            await relay.send(action)
    finally:
        await store.close()


async def test_switchboard_redelivery_after_interrupted_ack(tmp_path: Path) -> None:
    async with connected(tmp_path) as (ledger, _):
        await marker(tmp_path, "unavailable")
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "start",
            {"lane": "fixture-lane", "worker": "fixture", "branch": "feature", "work_items": []},
        )
        ready = asyncio.Event()

        async def progress(_elapsed: float, _cap: float) -> None:
            ready.set()

        watch = asyncio.create_task(
            ledger.wait_for_item("orchestrator", await ledger.head_seq(), 8, progress)
        )
        await ready.wait()
        (tmp_path / "available").touch()
        await marker(tmp_path, "interrupted")
        _, item = await watch
        assert item is not None
        assert item["kind"] == "inbound_message"
        acknowledged = await ledger.execute(
            "orchestrator", "ack", "ack-message", {"item": item["id"]}
        )
        assert acknowledged.result["state"] == "closed"
        events = [event for event in await ledger.events_after(0) if event.source == "switchboard"]
        assert len(events) == 1
        original = events[0]
        assert original.kind == "switchboard.message"
        assert original.upstream_id == "fixture-board:7"
        assert original.lane_id == "fixture-lane"
        assert original.fields == {"sender": "fixture-peer"}
        assert original.body == "worker message"
        assert not (tmp_path / "acked").exists()

    async with connected(tmp_path) as (ledger, _):
        await marker(tmp_path, "acked")
        assert (tmp_path / "acked").read_text() == "redelivered"
        events = [event for event in await ledger.events_after(0) if event.source == "switchboard"]
        assert events == [original]
        assert not (await ledger.read_board("orchestrator"))[1]


async def test_switchboard_send_reuses_action_key_after_interruption(tmp_path: Path) -> None:
    (tmp_path / "available").touch()
    (tmp_path / "acked").touch()

    async def enqueue(uow: UnitOfWork) -> str:
        cause = (await uow.append(NewEvent("core", "core.tick", "fixture-cause"))).event
        return await uow.enqueue_action(
            cause, SWITCHBOARD_SEND, {"to": "fixture-peer", "body": "message to worker"}
        )

    async with connected(tmp_path) as (ledger, runtime):
        action_key = await ledger.run("system", enqueue)
        task = asyncio.create_task(runtime.run_one_action())
        try:
            await marker(tmp_path, "send-first")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        pending = await ledger.pending_action()
        assert pending is not None
        assert pending.key == action_key
        assert pending.attempts == 0

    async with connected(tmp_path) as (ledger, runtime):
        async with asyncio.timeout(8):
            assert await runtime.run_one_action()
        assert await ledger.pending_action() is None
        first = json.loads((tmp_path / "send-first").read_text())
        assert first == json.loads((tmp_path / "send-retry").read_text())
        assert first["to"] == "fixture-peer"
        assert first["idempotency_key"] == action_key
        assert json.loads(first["body"]) == {"action_key": action_key, "body": "message to worker"}
