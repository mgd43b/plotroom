"""``plotroom serve`` in process: lock, SQLite floor and migration behavior."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from starlette.requests import Request

from conftest import authenticated_client, tool_json
from plotroom.adapters.mcp.server import execute_command
from plotroom.adapters.store_sqlite import SqliteStore, head_revision
from plotroom.app.config import Config
from plotroom.app.lock import LockError
from plotroom.app.portfile import PORT_FILE_NAME, PublishedListener, read_listener, read_port
from plotroom.app.serve import Daemon
from plotroom.app.tokens import TokenRegistry
from plotroom.core.catalog import build_catalog
from plotroom.core.commands import Command
from plotroom.core.roles import WriterRule
from plotroom.core.unit_of_work import UnitOfWork


def make_config(tmp_path: Path, *projects: str) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        host="127.0.0.1",
        port=0,
        allowed_hosts=("127.0.0.1:*",),
        projects=projects or ("demo",),
    )


def bearer(config: Config, project: str = "demo", role: str = "orchestrator") -> str:
    return TokenRegistry(config.data_dir).issue(project, role, local=False).bearer


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
        async with authenticated_client(f"http://127.0.0.1:{port}/mcp", bearer(config)) as client:
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
        async with authenticated_client(f"http://127.0.0.1:{port}/mcp", bearer(config)) as client:
            result = tool_json(await client.call_tool("snapshot", {}))
        assert result["ok"] is False
        assert result["error"]["code"] == "unavailable"
        assert "3.50.4" in result["error"]["message"]
        assert not (config.data_dir / "demo.db").exists()
    finally:
        await stop(daemon, task)


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_several_projects_are_migrated_and_each_token_only_sees_its_project(
    tmp_path: Path,
    mode: str,
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
            url = f"http://127.0.0.1:{port}/mcp"
            async with authenticated_client(url, bearer(config, project_id), mode=mode) as client:
                board = tool_json(await client.call_tool("snapshot", {}))
                assert board["data"]["project"] == project_id
                assert (
                    "project" not in (await client.list_tools()).tools[0].input_schema["properties"]
                )
    finally:
        await stop(daemon, task)


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_a_mutating_tool_uses_the_authenticated_role_and_project_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    catalog = build_catalog()

    async def record(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
        return {"role": uow.role}

    catalog.commands.register(
        Command("record_decision", (), record), rules=(WriterRule("orchestrator"),)
    )
    sdk = MCPServer("fixture")

    @sdk.tool()
    async def record_decision(  # pyright: ignore[reportUnusedFunction]
        request_id: str, ctx: Context[Any, Request]
    ) -> dict[str, Any]:
        return await execute_command(ctx, "record_decision", request_id, {})

    def fixture_server(_name: str) -> MCPServer[Any]:
        return sdk

    monkeypatch.setattr("plotroom.adapters.mcp.server.MCPServer", fixture_server)
    monkeypatch.setattr("plotroom.app.serve.build_catalog", lambda: catalog)
    config = make_config(tmp_path, "alpha", "beta")
    daemon = Daemon(config)
    task, port = await run_daemon(daemon)
    url = f"http://127.0.0.1:{port}/mcp"
    args = {"request_id": "test-request"}
    try:
        ledgers = daemon.ledgers
        async with authenticated_client(
            url, bearer(config, "alpha", "worker:ada"), mode=mode
        ) as client:
            denied = tool_json(await client.call_tool("record_decision", args))
        assert denied["error"]["code"] == "forbidden"
        assert await ledgers["alpha"].head_seq() == await ledgers["beta"].head_seq() == 0

        async with authenticated_client(url, bearer(config, "alpha"), mode=mode) as client:
            allowed = tool_json(await client.call_tool("record_decision", args))
        assert allowed == {"ok": True, "data": {"role": "orchestrator"}}
        assert await ledgers["alpha"].head_seq() == 1
        assert await ledgers["beta"].head_seq() == 0
    finally:
        await stop(daemon, task)


async def test_a_token_for_a_project_outside_the_running_config_gets_401(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    daemon = Daemon(config)
    task, port = await run_daemon(daemon)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/mcp",
                json={},
                headers={"authorization": f"Bearer {bearer(config, 'alpha')}"},
            )
        assert response.status_code == 401
        assert not response.content
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


async def test_a_failed_port_publication_closes_the_listener_and_frees_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    bound: list[int] = []

    def refuse(_data_dir: Path, port: int, *, host: str) -> None:
        bound.append(port)
        raise OSError("no space left on device")

    monkeypatch.setattr("plotroom.app.serve.write_port", refuse)
    with pytest.raises(OSError, match="no space left"):
        await asyncio.wait_for(Daemon(config).run(), timeout=30)
    [port] = bound
    with pytest.raises(ConnectionRefusedError):  # the socket was bound; it is closed again
        await asyncio.open_connection("127.0.0.1", port)
    monkeypatch.undo()
    again = Daemon(config)  # and the directory can be owned again
    task, _ = await run_daemon(again)
    await stop(again, task)


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


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
async def test_embedded_project_teardown_uses_the_original_signal_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signum: signal.Signals
) -> None:
    original_handler = signal.getsignal(signum)
    close_handlers: list[object] = []
    close = SqliteStore.close

    async def record_handler(store: SqliteStore) -> None:
        close_handlers.append(signal.getsignal(signum))
        await close(store)

    monkeypatch.setattr(SqliteStore, "close", record_handler)
    daemon = Daemon(make_config(tmp_path))
    task, _ = await run_daemon(daemon)
    await stop(daemon, task)
    assert close_handlers == [original_handler]


async def test_the_bound_port_is_published_with_the_pid_and_answers(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    daemon = Daemon(config)
    task, port = await run_daemon(daemon)
    try:
        assert port != 0, "the config's port of 0 became the port that was bound"
        assert read_port(config.data_dir) == (os.getpid(), port)
        assert read_listener(config.data_dir) == PublishedListener(os.getpid(), port, "127.0.0.1")
        async with authenticated_client(f"http://127.0.0.1:{port}/mcp", bearer(config)) as client:
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
