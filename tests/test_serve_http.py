"""End to end: a real ``plotroom serve`` answers an MCP SDK client over HTTP."""

from __future__ import annotations

import signal
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from textwrap import dedent
from typing import Any

import httpx
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import (
    CHILD_TIMEOUT,
    Child,
    authenticated_client,
    start_plotroom,
    start_python,
    tool_json,
)
from plotroom.adapters.store_sqlite import head_revision
from plotroom.app.cli import EXIT_CONFIG, EXIT_FAILURE, EXIT_LOCKED, main
from plotroom.app.portfile import PORT_FILE_NAME, read_listener, read_port
from plotroom.app.tokens import IssuedToken

EMPTY_BOARD: dict[str, Any] = {
    "ok": True,
    "data": {
        "project": "demo",
        "cursor": 0,
        "lanes": {},
        "open_items": {},
        "items": [],
        "left_out": 0,
    },
}
MODES = ["legacy", "auto"]


@pytest.mark.parametrize("mode", MODES)
async def test_snapshot_over_http_is_the_empty_board(
    server: tuple[Child, str], server_token: IssuedToken, mode: str
) -> None:
    _, url = server
    async with authenticated_client(url, server_token.bearer, mode=mode) as client:
        result = await client.call_tool("snapshot", {})
    assert result.is_error is False
    assert tool_json(result) == EMPTY_BOARD


@pytest.mark.parametrize("mode", MODES)
async def test_snapshot_through_the_stdio_proxy_is_the_same_board(
    server: tuple[Child, str], server_token: IssuedToken, mode: str
) -> None:
    _, url = server
    proxy = StdioServerParameters(
        command=sys.executable,
        args=["-m", "plotroom", "connect", "--stdio", "--url", url],
        env={"PLOTROOM_TOKEN": server_token.bearer},
    )
    async with Client(proxy, mode=mode) as client:
        result = await client.call_tool("snapshot", {})
    assert result.is_error is False
    assert tool_json(result) == EMPTY_BOARD


async def test_only_snapshot_is_registered(
    server: tuple[Child, str], server_token: IssuedToken
) -> None:
    _, url = server
    async with authenticated_client(url, server_token.bearer) as client:
        tools = await client.list_tools()
    assert [tool.name for tool in tools.tools] == ["snapshot"]


async def test_the_published_port_is_the_serving_port_and_answers(
    server: tuple[Child, str], config_path: Path
) -> None:
    child, url = server
    announced: dict[str, Any] = next(e for e in child.seen if e["msg"] == "serving")
    assert announced["port"] != 0
    assert read_port(config_path.parent / "data") == (child.process.pid, announced["port"])
    assert url == f"http://127.0.0.1:{announced['port']}/mcp"
    async with httpx.AsyncClient(trust_env=False) as http:
        assert (await http.head(url)).status_code == 401


def test_the_publication_and_private_socket_are_gone_after_the_server_is_stopped(
    server: tuple[Child, str], config_path: Path
) -> None:
    child, _ = server
    port_file = config_path.parent / "data" / PORT_FILE_NAME
    publication = read_listener(port_file.parent)
    assert publication is not None
    assert publication.local_socket is not None
    socket_path = Path(publication.local_socket)
    assert socket_path.is_socket()
    assert socket_path.parent.stat().st_mode & 0o777 == 0o700
    assert port_file.exists()
    child.stop()
    assert not port_file.exists()
    assert not socket_path.exists()


def test_sigterm_closes_the_open_project_file(server: tuple[Child, str], config_path: Path) -> None:
    child, _ = server  # the serving log means migrations are committed and the store is open
    project = config_path.parent / "data" / "demo.db"
    wal = project.with_suffix(".db-wal")
    shm = project.with_suffix(".db-shm")
    assert wal.stat().st_size > 0
    assert shm.exists()

    assert child.stop() == -signal.SIGTERM

    # Check before reopening SQLite, which could recover and remove the leftover files itself.
    assert not wal.exists(), "the daemon exited without closing the project store"
    assert not shm.exists()
    with closing(sqlite3.connect(project)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            head_revision(),
        )


@pytest.mark.parametrize("phase", ["startup", "teardown"])
def test_sigterm_does_not_interrupt_project_teardown(config_path: Path, phase: str) -> None:
    script = dedent("""\
        import logging
        import signal
        import sys

        from plotroom.adapters.store_sqlite import SqliteStore
        from plotroom.app.cli import main
        from plotroom.core.ledger import Ledger

        phase = sys.argv[1]
        log = logging.getLogger("plotroom")
        migrate, stop, close = SqliteStore.migrate, Ledger.stop, SqliteStore.close

        async def migrate_project(store):
            await migrate(store)
            if phase == "startup":
                signal.raise_signal(signal.SIGTERM)

        async def stop_ledger(ledger):
            await stop(ledger)
            log.info("test ledger drained")

        async def close_project(store):
            if phase == "teardown":
                signal.raise_signal(signal.SIGTERM)
            await close(store)
            log.info("test project closed")

        SqliteStore.migrate = migrate_project
        Ledger.stop = stop_ledger
        SqliteStore.close = close_project
        raise SystemExit(main(sys.argv[2:]))
        """)
    child = start_python("-c", script, phase, "serve", "--config", str(config_path))
    try:
        if phase == "teardown":
            child.wait_for_log("serving")
            assert child.stop() == -signal.SIGTERM
        else:
            assert child.process.wait(timeout=CHILD_TIMEOUT) == -signal.SIGTERM
        child.wait_for_log("test project closed")
        assert any(entry["msg"] == "test ledger drained" for entry in child.seen)
        if phase == "startup":
            assert all(entry["msg"] != "serving" for entry in child.seen)
    finally:
        child.stop()


def test_a_second_serve_on_the_same_directory_exits_with_the_lock_error(
    server: tuple[Child, str], config_path: Path
) -> None:
    first, _ = server
    second = start_plotroom("serve", "--config", str(config_path))
    try:
        entry = second.wait_for_log("data directory is locked")
        assert "another plotroom serve owns" in entry["reason"]
        assert second.process.wait(timeout=60) == EXIT_LOCKED
    finally:
        second.stop()
    assert first.process.poll() is None, "the first server must keep running"


def test_serving_log_line_is_json_and_leaks_nothing(server: tuple[Child, str]) -> None:
    child, _ = server
    announced: dict[str, Any] = next(e for e in child.seen if e["msg"] == "serving")
    assert announced["projects"] == ["demo"]
    assert set(announced) == {
        "ts",
        "level",
        "logger",
        "msg",
        "host",
        "port",
        "projects",
        "data_dir",
    }


async def test_a_foreign_host_header_is_refused(server: tuple[Child, str]) -> None:
    _, url = server
    async with httpx.AsyncClient() as http:
        response = await http.post(
            url,
            content=b"{}",
            headers={"host": "unknown.example"},
        )
    assert response.status_code == 421


@pytest.mark.parametrize("header", [{}, {"authorization": "Bearer invalid"}])
async def test_missing_or_invalid_credentials_get_a_bare_401(
    server: tuple[Child, str], header: dict[str, str]
) -> None:
    _, url = server
    async with httpx.AsyncClient() as http:
        response = await http.post(url, json={}, headers=header)
    assert response.status_code == 401
    assert response.content == b""
    assert "www-authenticate" not in response.headers


async def test_authenticated_requests_still_get_the_sdk_transport_checks(
    server: tuple[Child, str], server_token: IssuedToken
) -> None:
    _, url = server
    async with httpx.AsyncClient(
        headers={"authorization": f"Bearer {server_token.bearer}"}
    ) as http:
        content_type = await http.post(url, content=b"{}")
        origin = await http.post(url, json={}, headers={"origin": "https://unknown.example"})
        too_big = await http.post(
            url, content=b"x" * (4 * 1024 * 1024 + 1), headers={"content-type": "application/json"}
        )
    assert content_type.status_code == 400
    assert origin.status_code == 403
    assert too_big.status_code == 413


def test_connect_refuses_a_bearer_over_plaintext_remote_http(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PLOTROOM_TOKEN", "fixture-bearer")
    code = main(["connect", "--stdio", "--url", "http://plotroom.example.org/mcp"])
    assert code == EXIT_CONFIG
    assert "fixture-bearer" not in capsys.readouterr().err


def test_a_project_file_that_cannot_migrate_stops_serve_before_it_listens(
    tmp_path: Path, config_path: Path
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "demo.db") as db:
        db.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        db.execute("INSERT INTO alembic_version VALUES ('from-the-future')")
    child = start_plotroom("serve", "--config", str(config_path))
    try:
        entry = child.wait_for_log("serve failed")
        assert "from-the-future" in entry["exc"]
        assert child.process.wait(timeout=60) == EXIT_FAILURE
    finally:
        child.stop()
    assert not any(e["msg"] == "serving" for e in child.seen)


def test_connect_refuses_a_credential_in_a_plaintext_remote_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["connect", "--stdio", "--url", "http://plotroom.example.org/mcp?token=abc"])
    assert code == EXIT_CONFIG
    assert "abc" not in capsys.readouterr().err
