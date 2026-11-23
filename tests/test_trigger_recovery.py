"""A crash after the fire commits leaves the original action ready for dispatch."""

import asyncio
import signal
import sqlite3
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from conftest import child_env
from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.catalog import build_catalog
from plotroom.core.github import PULL_REQUESTS, ObservedPull, apply_pull_poll
from plotroom.core.ledger import Ledger

REPO = "demo/project"
TIME = "2026-11-23T10:00:00.000000Z"
PULL = ObservedPull(3, "demo", "a" * 40, REPO, "merged", TIME, "synthetic PR")
TRIGGER = {
    "predicate": {"pr_merged": {"number": 3}},
    "action": {"notify_orchestrator": {"text": "Merged"}},
}


@asynccontextmanager
async def opened(directory: Path) -> AsyncGenerator[Ledger]:
    store = SqliteStore("demo", directory)
    await store.migrate()
    ledger = Ledger(
        store, build_catalog(github_repository=REPO), FixedClock(), github_repository=REPO
    )
    ledger.start()
    try:
        yield ledger
    finally:
        await ledger.stop()
        await store.close()


async def observe(ledger: Ledger) -> None:
    cursor = await ledger.fetch_cursor(REPO, PULL_REQUESTS)
    assert await ledger.run("system", lambda uow: apply_pull_poll(uow, REPO, cursor, (PULL,), TIME))


async def pause_after_fire(directory: Path) -> None:
    async with opened(directory) as ledger:
        await ledger.execute("orchestrator", "trigger", "recovery", TRIGGER)
        await observe(ledger)
        action = await ledger.pending_action()
        assert action is not None
        print(f"FIRED {action.key}", flush=True)
        await asyncio.Future[None]()


async def test_restart_dispatches_the_committed_trigger_action_once(tmp_path: Path) -> None:
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        __file__,
        str(tmp_path),
        env=child_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        assert child.stdout is not None
        async with asyncio.timeout(30):
            line = (await child.stdout.readline()).decode().strip()
        marker, _, key = line.partition(" ")
        assert marker == "FIRED", line
        assert key, line
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()
    assert child.returncode == -signal.SIGKILL

    async with opened(tmp_path) as ledger:
        pending = await ledger.pending_action()
        assert pending is not None
        assert pending.key == key
        assert pending.attempts == 0
        assert not (await ledger.read_board("orchestrator"))[1]
        runtime = ProjectRuntime(ledger, FixedClock(), tick_seconds=60, action_attempts=2)
        cursor = await ledger.head_seq()

        async def progress(_elapsed: float, _total: float) -> None:
            pass

        runtime.start()
        try:
            _, wake = await ledger.wait_for_item("orchestrator", cursor, 10, progress)
            assert wake is not None
            assert wake["kind"] == "trigger_notification"
        finally:
            await runtime.stop()
        assert (await ledger.execute("orchestrator", "trigger", "recovery", TRIGGER)).replayed
        await observe(ledger)
        assert not await runtime.run_one_action()
        [item] = (await ledger.read_board("orchestrator"))[1]
        assert item.context == {"text": "Merged", "action_key": key}
        events = await ledger.events_after(0)
        assert sum(event.kind == "core.trigger_fired" for event in events) == 1
        assert sum(event.kind == "core.action_done" for event in events) == 1
    with sqlite3.connect(tmp_path / "demo.db") as db:
        assert db.execute("SELECT action_key, state, attempts FROM actions").fetchall() == [
            (key, "done", 1)
        ]


if __name__ == "__main__":
    asyncio.run(pause_after_fire(Path(sys.argv[1])))
