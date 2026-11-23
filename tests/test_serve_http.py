"""End to end: a real ``plotroom serve`` answers an MCP SDK client over HTTP."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import Child, start_plotroom, tool_json
from plotroom.app.cli import EXIT_CONFIG, EXIT_FAILURE, EXIT_LOCKED, main
from plotroom.app.daemon_start import speaks_mcp
from plotroom.app.portfile import PORT_FILE_NAME, read_port

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
async def test_snapshot_over_http_is_the_empty_board(server: tuple[Child, str], mode: str) -> None:
    _, url = server
    async with Client(url, mode=mode) as client:
        result = await client.call_tool("snapshot", {})
    assert result.is_error is False
    assert tool_json(result) == EMPTY_BOARD


@pytest.mark.parametrize("mode", MODES)
async def test_snapshot_through_the_stdio_proxy_is_the_same_board(
    server: tuple[Child, str], mode: str
) -> None:
    _, url = server
    proxy = StdioServerParameters(
        command=sys.executable, args=["-m", "plotroom", "connect", "--stdio", "--url", url]
    )
    async with Client(proxy, mode=mode) as client:
        result = await client.call_tool("snapshot", {})
    assert result.is_error is False
    assert tool_json(result) == EMPTY_BOARD


async def test_only_snapshot_is_registered(server: tuple[Child, str]) -> None:
    _, url = server
    async with Client(url, mode="legacy") as client:
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
    assert await speaks_mcp(url)


def test_the_port_file_is_gone_after_the_server_is_stopped(
    server: tuple[Child, str], config_path: Path
) -> None:
    child, _ = server
    port_file = config_path.parent / "data" / PORT_FILE_NAME
    assert port_file.exists()
    child.stop()  # SIGTERM: uvicorn shuts down, then re-raises the signal, so the code is -15
    assert not port_file.exists()


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
            headers={"content-type": "application/json", "host": "evil.example"},
        )
    assert response.status_code == 421


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
