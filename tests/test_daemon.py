"""``plotroom serve`` in process: lock, SQLite floor and migration behavior."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from mcp.client.client import Client

from conftest import tool_json
from plotroom.adapters.store_sqlite import SqliteStore, head_revision
from plotroom.app.config import Config
from plotroom.app.lock import LockError
from plotroom.app.portfile import PORT_FILE_NAME, read_port
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


async def test_cancelling_the_daemon_closes_the_listener_before_the_lock_is_released(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    daemon = Daemon(config)
    task, port = await run_daemon(daemon)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ConnectionRefusedError):  # nothing is listening any more
        await asyncio.open_connection("127.0.0.1", port)
    again = Daemon(config)  # and the directory can be owned again
    task2, _ = await run_daemon(again)
    await stop(again, task2)


async def test_cancelling_during_uvicorns_own_shutdown_waits_for_it_and_keeps_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    in_shutdown = asyncio.Event()
    finish = asyncio.Event()

    async def slow_shutdown(self: uvicorn.Server, sockets: Any = None) -> None:
        nonlocal calls
        calls += 1
        in_shutdown.set()
        await finish.wait()  # stands in for draining requests and the app's lifespan

    monkeypatch.setattr(uvicorn.Server, "shutdown", slow_shutdown)
    config = make_config(tmp_path)
    daemon = Daemon(config)
    task, _ = await run_daemon(daemon)
    daemon.stop()  # uvicorn starts its graceful shutdown
    await asyncio.wait_for(in_shutdown.wait(), timeout=30)
    task.cancel()
    task.cancel()  # a second cancel must not cut the shutdown short either
    await asyncio.sleep(0)
    assert not task.done(), "the daemon must wait for the shutdown to finish"
    with pytest.raises(LockError):  # the directory is still owned
        await Daemon(config).run()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=30)
    assert calls == 1
    again = Daemon(config)  # and it is free once the shutdown has completed
    task2, _ = await run_daemon(again)
    await stop(again, task2)


async def test_the_bound_port_is_published_with_the_pid_and_answers(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    daemon = Daemon(config)
    task, port = await run_daemon(daemon)
    try:
        assert port != 0, "the config's port of 0 became the port that was bound"
        assert read_port(config.data_dir) == (os.getpid(), port)
        async with Client(f"http://127.0.0.1:{port}/mcp", mode="legacy") as client:
            assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    finally:
        await stop(daemon, task)


async def test_the_port_file_is_gone_after_a_stop_and_after_a_cancel(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    port_file = config.data_dir / PORT_FILE_NAME
    daemon = Daemon(config)
    task, _ = await run_daemon(daemon)
    assert port_file.exists()
    await stop(daemon, task)
    assert not port_file.exists()
    again = Daemon(config)
    task, _ = await run_daemon(again)
    assert port_file.exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not port_file.exists()


async def test_a_leftover_port_file_is_replaced_once_the_lock_is_taken(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.data_dir.mkdir()
    (config.data_dir / PORT_FILE_NAME).write_text('{"pid": 1, "port": 9}', encoding="utf-8")
    daemon = Daemon(config)
    task, port = await run_daemon(daemon)
    try:
        assert read_port(config.data_dir) == (os.getpid(), port)
    finally:
        await stop(daemon, task)


async def test_a_refused_second_daemon_leaves_the_owners_port_file_alone(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    first = Daemon(config)
    task, port = await run_daemon(first)
    try:
        with pytest.raises(LockError):
            await Daemon(config).run()
        assert read_port(config.data_dir) == (os.getpid(), port)
    finally:
        await stop(first, task)
