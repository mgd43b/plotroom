import asyncio
import json
import sys
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest

from conftest import child_env, write_config
from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.config import load_config
from plotroom.app.runtime import ProjectRuntime
from plotroom.app.serve import Daemon
from plotroom.app.tokens import TokenRegistry
from plotroom.core.catalog import build_catalog
from plotroom.core.ledger import Ledger
from plotroom.core.next_items import ItemKind
from plotroom.core.watchers import Watchers


async def test_wait_returns_the_first_visible_change_and_no_cursor_starts_now(
    tmp_path: Path,
) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    catalog = build_catalog()
    catalog.register_item(ItemKind("fixture", 1, True, ("Inspect",)))
    ledger = Ledger(store, catalog, FixedClock())
    ledger.start()

    async def progress(elapsed: float, cap: float) -> None:
        pytest.fail(f"immediate wait should not report progress ({elapsed}, {cap})")

    try:
        await ledger.run(
            "system", lambda uow: uow.raise_item("fixture", "hidden", "worker:other", {})
        )
        first = await ledger.run(
            "system", lambda uow: uow.raise_item("fixture", "visible", "worker:fixture", {})
        )
        await ledger.run("system", lambda uow: uow.clear_item("fixture", "visible"))
        head = await ledger.head_seq()
        assert await ledger.wait_for_item("worker:fixture", None, 0, progress) == (head, None)
        position, found = await ledger.wait_for_item("worker:fixture", 0, 0, progress)
        assert position == first.seq
        assert found is not None
        assert found["state"] == "open"
        position, found = await ledger.wait_for_item("worker:fixture", position, 0, progress)
        assert position == head
        assert found is not None
        assert found["state"] == "closed"
        assert await ledger.wait_for_item("clerk", 0, 0, progress) == (head, None)
    finally:
        await ledger.stop()
        await store.close()


async def test_cli_watch_loops_caps_wakes_and_is_missing_on_next_tick(
    tmp_path: Path, token_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path)
    text = config.read_text().replace(
        "[projects.demo]", 'wait_cap = 2\n[projects.demo]\nwatchers = ["orchestrator"]'
    )
    config.write_text(text + "\n[projects.demo.workers.fixture]\n", encoding="utf-8")
    settings = load_config(config, {})
    token = TokenRegistry(settings.data_dir).issue("demo", "orchestrator", local=True)
    clock = FixedClock()
    daemon = Daemon(settings, clock=clock)
    arrivals: asyncio.Queue[None] = asyncio.Queue()
    original = Watchers.waiting

    @contextmanager
    def observed(self: Watchers, role: str) -> Generator[None]:
        with original(self, role):
            arrivals.put_nowait(None)
            yield

    monkeypatch.setattr(Watchers, "waiting", observed)
    serving = asyncio.create_task(daemon.run())
    try:
        async with asyncio.timeout(10):
            await daemon.wait_started()
        ledger = daemon.ledgers["demo"]
        watcher = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "plotroom",
            "watch",
            "--config",
            str(config),
            "--token-id",
            token.token_id,
            env=child_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(12):
                for _ in range(3):
                    await arrivals.get()
                    assert watcher.returncode is None
            runtime = ProjectRuntime(ledger, clock, tick_seconds=10, action_attempts=2)
            clock.advance(10)
            assert await runtime.tick_if_due()
            assert not (await ledger.read_board("orchestrator"))[1]
            await ledger.execute(
                "orchestrator",
                "lane_start",
                "start",
                {"lane": "fixture", "worker": "fixture", "branch": "demo", "work_items": []},
            )
            await ledger.execute(
                "worker:fixture",
                "lane_update",
                "waiting",
                {"lane": "fixture", "waiting_on": "orchestrator"},
            )
            async with asyncio.timeout(1):
                stdout, stderr = await watcher.communicate()
            assert watcher.returncode == 0, stderr.decode()
            result = json.loads(stdout)
            assert result["item"]["kind"] == "lane_waiting"
            clock.advance(10)
            assert await runtime.tick_if_due()
            assert {i.kind for i in (await ledger.read_board("orchestrator"))[1]} == {
                "lane_waiting",
                "watcher_not_running",
            }
        finally:
            if watcher.returncode is None:
                with suppress(ProcessLookupError):
                    watcher.kill()
                await watcher.wait()
    finally:
        daemon.stop()
        async with asyncio.timeout(10):
            await serving
