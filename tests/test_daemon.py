"""``plotroom serve`` in process: lock, SQLite floor and migration behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.client.client import Client

from conftest import tool_json
from plotroom.adapters.store_sqlite import SqliteStore, head_revision
from plotroom.app.config import Config
from plotroom.app.lock import LockError
from plotroom.app.serve import Daemon


def make_config(tmp_path: Path, *projects: str) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        host="127.0.0.1",
        port=0,
        allowed_hosts=("127.0.0.1:*",),
        projects=projects or ("demo",),
    )


async def run_daemon(daemon: Daemon) -> tuple[asyncio.Task[None], int]:
    task = asyncio.create_task(daemon.run())
    started = asyncio.create_task(daemon.wait_started())
    done, _ = await asyncio.wait({task, started}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        started.cancel()
        task.result()
        pytest.fail("the daemon exited before it started serving")
    return task, started.result()


async def stop(daemon: Daemon, task: asyncio.Task[None]) -> None:
    daemon.stop()
    await asyncio.wait_for(task, timeout=30)


async def test_the_project_file_is_migrated_before_serving(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    daemon = Daemon(config)
    task, _ = await run_daemon(daemon)
    try:
        store = SqliteStore("demo", config.data_dir)
        try:
            assert await store.revision() == head_revision()
        finally:
            await store.close()
    finally:
        await stop(daemon, task)


async def test_a_second_daemon_on_the_directory_is_refused_and_the_first_unaffected(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    first = Daemon(config)
    task, port = await run_daemon(first)
    try:
        with pytest.raises(LockError):
            await Daemon(config).run()
        async with Client(f"http://127.0.0.1:{port}/mcp", mode="legacy") as client:
            assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    finally:
        await stop(first, task)
    # the lock is released with the server: a new one can start
    again = Daemon(config)
    task, _ = await run_daemon(again)
    await stop(again, task)


async def test_old_sqlite_serves_unavailable_and_never_opens_a_project_file(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    daemon = Daemon(config, sqlite_version="3.50.4")
    task, port = await run_daemon(daemon)
    try:
        async with Client(f"http://127.0.0.1:{port}/mcp", mode="legacy") as client:
            result = tool_json(await client.call_tool("snapshot", {}))
        assert result["ok"] is False
        assert result["error"]["code"] == "unavailable"
        assert "3.50.4" in result["error"]["message"]
        assert not (config.data_dir / "demo.db").exists()
    finally:
        await stop(daemon, task)


async def test_several_projects_are_all_migrated_but_snapshot_needs_a_credential(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, "alpha", "beta")
    daemon = Daemon(config)
    task, port = await run_daemon(daemon)
    try:
        for project_id in config.projects:
            store = SqliteStore(project_id, config.data_dir)
            try:
                assert await store.revision() == head_revision()
            finally:
                await store.close()
        async with Client(f"http://127.0.0.1:{port}/mcp", mode="legacy") as client:
            result = tool_json(await client.call_tool("snapshot", {}))
        assert result["error"]["code"] == "invalid"
    finally:
        await stop(daemon, task)
