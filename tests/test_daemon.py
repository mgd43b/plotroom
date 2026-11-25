"""``plotroom serve`` in process: lock, SQLite floor and migration behavior."""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import tempfile
from collections.abc import Mapping
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
from plotroom.app.config import Config, ConfigError
from plotroom.app.lock import LockError
from plotroom.app.portfile import (
    PORT_FILE_NAME,
    directory_identity,
    published_socket,
    read_listener,
    read_port,
)
from plotroom.app.serve import Daemon
from plotroom.app.tokens import TokenRegistry
from plotroom.core.catalog import Catalog, build_catalog
from plotroom.core.commands import Command
from plotroom.core.roles import WriterRule
from plotroom.core.trigger_actions import DeliveryRoute
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
        async with httpx.AsyncClient(trust_env=False) as http:
            assert (await http.get(f"http://127.0.0.1:{port}/healthz")).status_code == 200
            ready = await http.get(f"http://127.0.0.1:{port}/readyz/demo")
            assert ready.status_code == 503
            assert ready.json() == {"status": "unavailable", "projects": {"demo": "unavailable"}}
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

    def fixture_catalog(
        _workers: frozenset[str],
        *,
        github_repository: str | None = None,
        delivery_routes: Mapping[str, DeliveryRoute] | None = None,
        image_jobs: tuple[str, ...] = (),
        lane_idle_minutes: int = 15,
        human_login: str | None = None,
    ) -> Catalog:
        return catalog

    monkeypatch.setattr("plotroom.app.serve.build_catalog", fixture_catalog)
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


async def test_cancelling_while_the_unix_listener_starts_cleans_up_its_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    listening = asyncio.Event()
    hold = asyncio.Event()
    start_serving = asyncio.Server.start_serving
    paths: list[Path] = []

    async def start_then_wait(listener: asyncio.Server) -> None:
        await start_serving(listener)
        if listener.sockets[0].family == socket.AF_UNIX:
            paths.append(Path(str(listener.sockets[0].getsockname())))
            listening.set()
            await hold.wait()

    monkeypatch.setattr(asyncio.Server, "start_serving", start_then_wait)
    task = asyncio.create_task(Daemon(config).run())
    try:
        await asyncio.wait_for(listening.wait(), timeout=10)
        [path] = paths
        assert path.is_socket()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not path.exists()
    assert not path.parent.exists()


async def test_startup_cannot_unpublish_or_displace_the_replacement_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    tcp_started = asyncio.Event()
    resume = asyncio.Event()
    startup = uvicorn.Server.startup

    async def pause_first(
        server: uvicorn.Server, sockets: list[socket.socket] | None = None
    ) -> None:
        await startup(server, sockets)
        if not tcp_started.is_set():
            tcp_started.set()
            await resume.wait()

    monkeypatch.setattr(uvicorn.Server, "startup", pause_first)
    previous = Daemon(config, sqlite_version="3.0.0")
    previous_task = asyncio.create_task(previous.run())
    try:
        await asyncio.wait_for(tcp_started.wait(), timeout=10)
        moved = tmp_path / "moved"
        config.data_dir.rename(moved)
        replacement = Daemon(config, sqlite_version="3.0.0")
        replacement_task, _ = await run_daemon(replacement)
        try:
            published = read_listener(config.data_dir)
            assert published is not None
            assert published.local_socket is not None
            resume.set()
            with pytest.raises(ConfigError, match="data directory changed during startup"):
                await asyncio.wait_for(previous_task, timeout=10)
            assert read_listener(config.data_dir) == published
            assert await asyncio.to_thread(Path(published.local_socket).is_socket)
            assert read_listener(moved) is None
        finally:
            await stop(replacement, replacement_task)
    finally:
        if not previous_task.done():
            previous_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await previous_task


async def test_shutdown_removes_the_socket_from_a_renamed_data_directory() -> None:
    with tempfile.TemporaryDirectory(prefix="plotroom-test-", dir="/tmp") as shared:
        root = await asyncio.to_thread(Path(shared).resolve)
        config = make_config(root)
        daemon = Daemon(config, sqlite_version="3.0.0")
        task, _ = await run_daemon(daemon)
        try:
            published = read_listener(config.data_dir)
            assert published is not None
            assert published.local_socket is not None
            parent = Path(published.local_socket).parent
            assert parent.parent == config.data_dir
            moved = root / "moved"
            await asyncio.to_thread(config.data_dir.rename, moved)
            moved_parent = moved / parent.name
            assert await asyncio.to_thread(moved_parent.is_dir)
        finally:
            await stop(daemon, task)
        assert not await asyncio.to_thread(moved_parent.exists)
        assert read_listener(moved) is None


async def test_public_http_serves_when_the_private_listener_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    credential = bearer(config)

    def unavailable(_data_dir: Path, *, directory_fd: int | None = None) -> Path:
        raise PermissionError("private socket directory unavailable")

    monkeypatch.setattr("plotroom.app.serve.prepare_local_socket", unavailable)
    daemon = Daemon(config)
    task, port = await run_daemon(daemon)
    try:
        published = read_listener(config.data_dir)
        assert published is not None
        assert published.local_unavailable
        assert published.local_socket is None
        async with authenticated_client(f"http://127.0.0.1:{port}/mcp", credential) as client:
            assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    finally:
        await stop(daemon, task)


async def test_a_failed_port_publication_closes_the_listener_and_frees_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    bound: list[tuple[int, Path]] = []

    def refuse(_data_dir: Path, port: int, *, local_socket: Path, **_publication: Any) -> None:
        bound.append((port, local_socket))
        raise OSError("no space left on device")

    monkeypatch.setattr("plotroom.app.serve.write_port", refuse)
    with pytest.raises(OSError, match="no space left"):
        await asyncio.wait_for(Daemon(config).run(), timeout=30)
    [(port, socket_path)] = bound
    with pytest.raises(ConnectionRefusedError):  # the socket was bound; it is closed again
        await asyncio.open_connection("127.0.0.1", port)
    assert not socket_path.exists()
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
    publication = read_listener(config.data_dir)
    assert publication is not None
    assert publication.local_socket is not None
    port_file = config.data_dir / PORT_FILE_NAME
    assert await asyncio.to_thread(port_file.is_file)
    daemon.stop()  # uvicorn starts its graceful shutdown
    try:
        await asyncio.wait_for(in_shutdown.wait(), timeout=30)
        assert not await asyncio.to_thread(port_file.exists)
        assert not await asyncio.to_thread(Path(publication.local_socket).parent.exists)
        task.cancel()
        task.cancel()  # a second cancel must not cut the shutdown short either
        await asyncio.sleep(0)
        assert not task.done(), "the daemon must wait for the shutdown to finish"
        with pytest.raises(LockError):  # the directory is still owned
            await Daemon(config).run()
    finally:
        finish.set()
        if task.cancelling():
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=30)
        else:
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
        publication = read_listener(config.data_dir)
        assert publication is not None
        assert publication.host == "127.0.0.1"
        assert publication.local_socket is not None
        path = published_socket(
            config.data_dir, directory_identity(config.data_dir), publication.local_socket
        )
        assert path is not None
        assert path.is_socket()
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
