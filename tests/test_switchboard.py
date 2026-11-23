import asyncio
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.switchboard import Switchboard
from plotroom.core.catalog import build_catalog
from plotroom.core.ledger import Ledger


@asynccontextmanager
async def connected(directory: Path) -> AsyncGenerator[Ledger]:
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
    try:
        yield ledger
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await ledger.stop()
        await store.close()


async def marker(directory: Path, name: str) -> None:
    async with asyncio.timeout(8):
        while not (directory / name).exists():  # noqa: ASYNC110 - observed across subprocesses
            await asyncio.sleep(0.02)


async def test_switchboard_redelivery_after_interrupted_ack(tmp_path: Path) -> None:
    async with connected(tmp_path) as ledger:
        await marker(tmp_path, "unavailable")
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "start",
            {"lane": "fixture-lane", "worker": "fixture", "branch": "feature", "work_items": []},
        )
        (tmp_path / "available").touch()
        await marker(tmp_path, "interrupted")
        events = [event for event in await ledger.events_after(0) if event.source == "switchboard"]
        assert len(events) == 1
        original = events[0]
        assert original.kind == "switchboard.message"
        assert original.upstream_id == "fixture-board:7"
        assert original.lane_id == "fixture-lane"
        assert original.fields == {"sender": "fixture-peer"}
        assert original.body == "worker message"
        assert not (tmp_path / "acked").exists()

    async with connected(tmp_path) as ledger:
        await marker(tmp_path, "acked")
        assert (tmp_path / "acked").read_text() == "redelivered"
        events = [event for event in await ledger.events_after(0) if event.source == "switchboard"]
        assert events == [original]
