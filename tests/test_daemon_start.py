"""On-demand daemon start for ``plotroom connect --stdio``: environment, races, refusals, restarts."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import child_env, start_plotroom, tool_json, write_config
from plotroom.app.cli import EXIT_LOCKED, main
from plotroom.app.config import load_config
from plotroom.app.daemon_start import (
    LOG_FILE_NAME,
    answers_ping,
    current_url,
    daemon_env,
    ensure_daemon,
    logged_reason,
    serve_command,
    speaks_mcp,
    start_daemon,
)
from plotroom.app.lock import LOCK_FILE_NAME, DataDirLock, LockError
from plotroom.app.portfile import PORT_FILE_NAME, read_port, write_port
from plotroom.app.tokens import TokenRegistry, select_local_token
from rawproxy import ERAS, RawProxy, connected, first_request, spawn_proxy

MODES = ["legacy", "auto"]
WAIT = 60.0
pytestmark = pytest.mark.usefixtures("token_home")
EMPTY_BOARD_DATA: dict[str, Any] = {
    "project": "demo",
    "cursor": 0,
    "lanes": {},
    "open_items": {},
    "items": [],
    "left_out": 0,
}


def owner_pid(data_dir: Path) -> int:
    return int((data_dir / LOCK_FILE_NAME).read_text(encoding="utf-8"))


def is_locked(data_dir: Path) -> bool:
    lock = DataDirLock(data_dir)
    try:
        lock.acquire()
    except LockError:
        return True
    lock.release()
    return False


def wait_until_unlocked(data_dir: Path) -> None:
    deadline = time.monotonic() + WAIT
    while is_locked(data_dir):
        assert time.monotonic() < deadline, "the daemon did not release the data directory"
        time.sleep(0.02)


def stop_owner(data_dir: Path) -> None:
    if (data_dir / LOCK_FILE_NAME).exists() and is_locked(data_dir):
        os.kill(owner_pid(data_dir), signal.SIGTERM)
        wait_until_unlocked(data_dir)


@pytest.fixture
def data_dir(tmp_path: Path) -> Iterator[Path]:
    """The test's data directory; whatever daemon a client started on demand is stopped after."""
    directory = tmp_path / "data"
    try:
        yield directory
    finally:
        stop_owner(directory)


def proxy_params(config: Path, *extra: str) -> StdioServerParameters:
    settings = load_config(config, {})
    if select_local_token(settings.data_dir, None) is None:
        TokenRegistry(settings.data_dir).issue(settings.projects[0], "orchestrator")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "plotroom", "connect", "--stdio", "--config", str(config), *extra],
        env=child_env(),
    )


def serving_lines(data_dir: Path) -> list[dict[str, Any]]:
    lines = (data_dir / LOG_FILE_NAME).read_text(encoding="utf-8").splitlines()
    entries: list[dict[str, Any]] = [json.loads(line) for line in lines]
    return [entry for entry in entries if entry["msg"] == "serving"]


# --- the environment ----------------------------------------------------------------------


def test_the_daemon_environment_keeps_the_runtime_and_config_names_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    env = {
        "PATH": "/usr/bin",
        "HOME": "/home/someone",
        "LANG": "en_US.UTF-8",
        "PLOTROOM_PORT": "9000",
        "PLOTROOM_PROJECTS": "demo",
        "PLOTROOM_DATA_DIR": "relative/data",
        "PLOTROOM_CONFIG": "cfg.toml",
        "ANTHROPIC_API_KEY": "client-secret",
        "OPENAI_API_KEY": "client-secret",
        "GITHUB_TOKEN": "client-secret",
        "CLAUDECODE": "1",
        "PLOTROOM_DEMO_TOKEN": "adapter-secret",
        "PLOTROOM_TOKEN": "proxy-secret",
        "PLOTROOM_TOKEN_ID": "proxy-id",
        "PYTHONPATH": "/somewhere",
    }
    clean = daemon_env(env)
    assert set(clean) == {
        "PATH",
        "HOME",
        "LANG",
        "PLOTROOM_PORT",
        "PLOTROOM_PROJECTS",
        "PLOTROOM_DATA_DIR",
        "PLOTROOM_CONFIG",
    }
    assert clean["PLOTROOM_DATA_DIR"] == str((tmp_path / "relative" / "data").resolve())
    assert clean["PLOTROOM_CONFIG"] == str((tmp_path / "cfg.toml").resolve())
    assert "client-secret" not in "".join(clean.values())


def test_the_daemon_command_keeps_the_working_directory_off_the_module_path() -> None:
    assert serve_command(None)[:3] == [sys.executable, "-P", "-m"]


def test_a_started_daemon_runs_detached_with_only_the_clean_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIENT_SECRET_CANARY", "leak")
    dump = tmp_path / "env.json"
    script = (
        "import json, os, sys\n"
        "json.dump({'env': dict(os.environ), 'sid': os.getsid(0), 'pid': os.getpid(),"
        " 'stdin': sys.stdin.read()}, open(sys.argv[1], 'w'))\n"
    )
    child = start_daemon(
        [sys.executable, "-c", script, str(dump)],
        daemon_env(os.environ),
        tmp_path / "work",
        tmp_path / "work" / LOG_FILE_NAME,
    )
    assert child.wait(timeout=WAIT) == 0
    seen = json.loads(dump.read_text(encoding="utf-8"))
    assert "CLIENT_SECRET_CANARY" not in seen["env"]
    assert seen["sid"] == seen["pid"], "the daemon leads its own session, apart from the client's"
    assert seen["stdin"] == "", "the daemon has no stdin to share with the client"


def test_a_logged_traceback_adds_its_last_line_and_bare_output_stands_in_when_nothing_was_logged(
    tmp_path: Path,
) -> None:
    log = tmp_path / LOG_FILE_NAME
    entry = {"level": "error", "msg": "serve failed", "exc": "Traceback...\nOSError: bind failed"}
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    assert logged_reason(log, 0) == "serve failed: OSError: bind failed"
    log.write_text("Traceback (most recent call last):\nModuleNotFoundError: no uvicorn\n", "utf-8")
    assert logged_reason(log, 0) == "ModuleNotFoundError: no uvicorn"


def test_the_last_logged_error_after_an_offset_is_the_reason(tmp_path: Path) -> None:
    log = tmp_path / LOG_FILE_NAME
    old = json.dumps({"level": "error", "msg": "old failure"}) + "\n"
    new = (
        json.dumps({"level": "info", "msg": "serving"})
        + "\nnot json at all\n"
        + json.dumps({"level": "error", "msg": "data directory is locked", "reason": "pid 7"})
        + "\n"
    )
    log.write_text(old + new, encoding="utf-8")
    assert logged_reason(log, len(old)) == "data directory is locked: pid 7"
    assert logged_reason(tmp_path / "missing.log", 0) is None


def test_the_daemon_command_names_an_absolute_log_file(tmp_path: Path) -> None:
    command = serve_command(None, tmp_path / "data" / LOG_FILE_NAME)
    assert command[command.index("--log-file") + 1] == str(
        (tmp_path / "data" / LOG_FILE_NAME).resolve()
    )
    assert "--log-file" not in serve_command(None)


def test_the_reason_is_read_from_the_start_of_a_file_that_rolled_since_the_offset(
    tmp_path: Path,
) -> None:
    log = tmp_path / LOG_FILE_NAME
    before = json.dumps({"level": "error", "msg": "old failure", "pad": "x" * 100}) + "\n"
    log.write_text(before, encoding="utf-8")
    offset = log.stat().st_size
    log.write_text(  # rolled: the old file is `.1` now, and the new one is smaller than the offset
        json.dumps({"level": "error", "msg": "new failure"}) + "\n", encoding="utf-8"
    )
    assert log.stat().st_size < offset
    assert logged_reason(log, offset) == "new failure"


# --- the readiness probe ------------------------------------------------------------------

PING_RESULT = '{"jsonrpc":"2.0","id":"probe","result":{}}'


def test_only_the_result_for_the_probes_own_ping_counts_as_an_answer() -> None:
    assert answers_ping(PING_RESULT)
    assert answers_ping(f"event: message\ndata: {PING_RESULT}\n\n")
    assert not answers_ping('{"jsonrpc":"2.0","id":"probe","error":{"code":-32601,"message":"x"}}')
    assert not answers_ping('{"jsonrpc":"2.0","id":"other","result":{}}')
    assert not answers_ping("<html>jsonrpc</html>")
    assert not answers_ping("")


async def answer_with(raw: bytes, *, then_stall: bool = False) -> tuple[asyncio.Server, str]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(raw)
        await writer.drain()
        if then_stall:
            await asyncio.sleep(WAIT)  # trickles nothing more, until the test ends
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/mcp"


def http_reply(status: str, body: str, content_type: str = "application/json") -> bytes:
    payload = body.encode()
    head = f"HTTP/1.1 {status}\r\ncontent-type: {content_type}\r\ncontent-length: {len(payload)}"
    return f"{head}\r\nconnection: close\r\n\r\n".encode() + payload


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (http_reply("200 OK", PING_RESULT), True),
        (http_reply("200 OK", f"data: {PING_RESULT}\n\n", "text/event-stream"), True),
        (http_reply("401 Unauthorized", ""), True),
        (http_reply("421 Misdirected Request", ""), False),
        (http_reply("500 Internal Server Error", PING_RESULT), False),
        (http_reply("200 OK", '{"jsonrpc":"2.0","id":"x","result":{}}'), False),
        (http_reply("200 OK", "<html>hello</html>", "text/html"), False),
    ],
)
async def test_the_probe_accepts_a_successful_ping_or_a_401(reply: bytes, expected: bool) -> None:
    server, url = await answer_with(reply)
    try:
        assert await speaks_mcp(url) is expected
    finally:
        server.close()


async def test_the_probe_is_bounded_in_total_not_per_read() -> None:
    partial = b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n\r\n: keepalive\n"
    server, url = await answer_with(partial, then_stall=True)
    try:
        started = time.monotonic()
        assert await speaks_mcp(url, within=0.3) is False
        assert time.monotonic() - started < 5
    finally:
        server.close()


# --- starting on demand --------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
async def test_a_client_gets_snapshot_with_the_daemon_started_on_demand(
    tmp_path: Path, data_dir: Path, mode: str
) -> None:
    config = write_config(tmp_path)
    assert not (data_dir / LOCK_FILE_NAME).exists(), "no daemon before the client connects"
    async with Client(proxy_params(config), mode=mode) as client:
        result = await client.call_tool("snapshot", {})
    assert tool_json(result) == {"ok": True, "data": EMPTY_BOARD_DATA}
    assert is_locked(data_dir), "the daemon outlives the proxy that started it"
    assert len(serving_lines(data_dir)) == 1


async def test_a_package_in_the_data_directory_cannot_shadow_plotroom(
    tmp_path: Path, data_dir: Path
) -> None:
    (data_dir / "plotroom").mkdir(parents=True)
    (data_dir / "plotroom" / "__init__.py").write_text("raise SystemExit(99)\n", encoding="utf-8")
    config = write_config(tmp_path)
    async with Client(proxy_params(config), mode="auto") as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True


@pytest.mark.parametrize("mode", MODES)
async def test_a_running_daemon_is_used_and_none_is_started(
    tmp_path: Path, data_dir: Path, mode: str
) -> None:
    config = write_config(tmp_path)
    server = start_plotroom("serve", "--config", str(config))
    try:
        server.wait_for_log("serving")
        pid = owner_pid(data_dir)
        async with Client(proxy_params(config), mode=mode) as client:
            result = await client.call_tool("snapshot", {})
        assert tool_json(result)["ok"] is True
        assert owner_pid(data_dir) == pid
        assert not (data_dir / LOG_FILE_NAME).exists(), "no process was started"
    finally:
        server.stop()


@pytest.mark.parametrize("era", ERAS)
async def test_revoking_a_token_is_visible_to_http_and_the_proxy_without_restart(
    tmp_path: Path, data_dir: Path, era: str
) -> None:
    config = write_config(tmp_path)
    registry = TokenRegistry(data_dir)
    token = registry.issue("demo", "orchestrator")
    server = start_plotroom("serve", "--config", str(config))
    try:
        port = server.wait_for_log("serving")["port"]
        proxy = await spawn_proxy("--config", str(config))
        try:
            call = await connected(proxy, era)
            proxy.send(call(1, "snapshot", {}))
            assert (await proxy.answer(1))["result"]["isError"] is False
            assert main(["token", "revoke", "--config", str(config), token.token_id]) == 0
            proxy.send(call(2, "snapshot", {}))
            assert "HTTP 401" in (await proxy.answer(2))["error"]["message"]
            async with httpx.AsyncClient() as http:
                response = await http.post(
                    f"http://127.0.0.1:{port}/mcp",
                    json={},
                    headers={"authorization": f"Bearer {token.bearer}"},
                )
            assert response.status_code == 401
            assert response.content == b""
            assert "www-authenticate" not in response.headers
        finally:
            await proxy.close()

        stale = await spawn_proxy("--config", str(config), env={"PLOTROOM_TOKEN": token.bearer})
        assert "HTTP 401" in await first_error(stale, era)
        assert published(data_dir) == (server.process.pid, port)
        assert not (data_dir / LOG_FILE_NAME).exists(), "401 readiness should skip daemon startup"
    finally:
        server.stop()


async def test_two_clients_starting_at_once_share_one_daemon(
    tmp_path: Path, data_dir: Path
) -> None:
    config = write_config(tmp_path)

    async def one_call(mode: str) -> dict[str, Any]:
        async with Client(proxy_params(config), mode=mode) as client:
            return tool_json(await client.call_tool("snapshot", {}))

    first, second = await asyncio.gather(one_call("legacy"), one_call("auto"))
    assert first == second == {"ok": True, "data": EMPTY_BOARD_DATA}
    assert len(serving_lines(data_dir)) == 1, "one daemon won the lock"


@pytest.mark.parametrize("mode", MODES)
async def test_a_daemon_that_died_mid_session_is_started_again(
    tmp_path: Path, data_dir: Path, mode: str
) -> None:
    config = write_config(tmp_path)
    async with Client(proxy_params(config), mode=mode) as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
        first = owner_pid(data_dir)
        stop_owner(data_dir)
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    assert owner_pid(data_dir) != first
    assert len(serving_lines(data_dir)) == 2


# --- the published port ---------------------------------------------------------------------


def published(data_dir: Path) -> tuple[int, int]:
    found = read_port(data_dir)
    assert found is not None, "the daemon published no port"
    return found


def dead_pid() -> int:
    """The pid of a process that has exited."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=WAIT)
    return child.pid


def leave_stale_file(data_dir: Path, pid: int, port: int) -> None:
    data_dir.mkdir(parents=True)
    (data_dir / PORT_FILE_NAME).write_text(json.dumps({"pid": pid, "port": port}), encoding="utf-8")


async def test_a_daemon_started_on_demand_publishes_the_port_it_serves_on(
    tmp_path: Path, data_dir: Path
) -> None:
    config = write_config(tmp_path)
    async with Client(proxy_params(config), mode="auto") as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    pid, port = published(data_dir)
    assert pid == owner_pid(data_dir)
    [announced] = serving_lines(data_dir)
    assert announced["port"] == port
    assert await speaks_mcp(f"http://127.0.0.1:{port}/mcp")


async def test_a_killed_daemons_file_is_not_trusted_and_a_new_client_reaches_the_new_daemon(
    tmp_path: Path, data_dir: Path
) -> None:
    config = write_config(tmp_path)
    async with Client(proxy_params(config), mode="auto") as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    first, first_port = published(data_dir)
    os.kill(first, signal.SIGKILL)
    await asyncio.to_thread(wait_until_unlocked, data_dir)
    assert published(data_dir) == (first, first_port), "a killed daemon leaves its file behind"
    async with Client(proxy_params(config), mode="auto") as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    second, second_port = published(data_dir)
    assert second == owner_pid(data_dir) != first
    assert len(serving_lines(data_dir)) == 2
    assert await speaks_mcp(f"http://127.0.0.1:{second_port}/mcp")


@pytest.mark.parametrize("owner", ["a dead pid", "a live pid that holds no lock"])
async def test_a_stale_file_naming_a_foreign_mcp_listener_is_never_used(
    tmp_path: Path, data_dir: Path, owner: str
) -> None:
    requests: list[int] = []

    async def foreign_mcp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        requests.append(1)
        await reader.readuntil(b"\r\n\r\n")
        writer.write(http_reply("200 OK", PING_RESULT))  # it would pass for the daemon
        await writer.drain()
        writer.close()

    foreign = await asyncio.start_server(foreign_mcp, "127.0.0.1", 0)
    try:
        foreign_port = int(foreign.sockets[0].getsockname()[1])
        stale_pid = dead_pid() if owner == "a dead pid" else os.getpid()
        await asyncio.to_thread(leave_stale_file, data_dir, stale_pid, foreign_port)
        config = write_config(tmp_path)
        async with Client(proxy_params(config), mode="auto") as client:
            assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
        pid, port = published(data_dir)
        assert pid == owner_pid(data_dir)
        assert port != foreign_port
        assert requests == [], "the foreign listener received nothing"
    finally:
        foreign.close()


async def test_two_data_directories_each_reach_their_own_daemon(tmp_path: Path) -> None:
    names = ("alpha", "beta")
    configs: dict[str, Path] = {}
    for name in names:
        (tmp_path / name).mkdir()
        configs[name] = write_config(tmp_path / name, projects=(name,))

    async def snapshot_project(name: str) -> str:
        async with Client(proxy_params(configs[name]), mode="auto") as client:
            data: dict[str, Any] = tool_json(await client.call_tool("snapshot", {}))["data"]
        return str(data["project"])

    try:
        seen = await asyncio.gather(*(snapshot_project(name) for name in names))
        assert seen == list(names)
        alpha, beta = (published(tmp_path / name / "data") for name in names)
        assert alpha[0] != beta[0], "two daemons"
        assert alpha[1] != beta[1], "on two ports"
    finally:
        for name in names:
            await asyncio.to_thread(stop_owner, tmp_path / name / "data")


async def test_the_proxy_selects_the_requested_project_token_by_id(
    tmp_path: Path, data_dir: Path
) -> None:
    config = write_config(tmp_path, projects=("alpha", "beta"))
    registry = TokenRegistry(data_dir)
    first = registry.issue("alpha", "orchestrator")
    second = registry.issue("beta", "worker:ada")
    selectors: list[tuple[str, list[str], dict[str, str]]] = [
        ("alpha", ["--token-id", first.token_id], {}),
        ("beta", [], {"PLOTROOM_TOKEN_ID": second.token_id}),
    ]
    for expected, flags, env in selectors:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "plotroom", "connect", "--stdio", "--config", str(config), *flags],
            env={**child_env(), **env},
        )
        async with Client(params, mode="auto") as client:
            board = tool_json(await client.call_tool("snapshot", {}))
            assert board["data"]["project"] == expected
    assert len(serving_lines(data_dir)) == 1


async def test_the_proxy_preserves_an_explicitly_allowed_localhost_header(
    tmp_path: Path, data_dir: Path
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'data_dir = "data"\nhost = "localhost"\nport = 0\n'
        'allowed_hosts = ["localhost:*"]\n[projects.demo]\n',
        encoding="utf-8",
    )
    async with Client(proxy_params(config), mode="auto") as client:
        assert tool_json(await client.call_tool("snapshot", {}))["data"]["project"] == "demo"
    assert len(serving_lines(data_dir)) == 1


# --- reading the port back ------------------------------------------------------------------


async def test_a_fixed_port_is_the_configs_own_url_whatever_the_directory_holds(
    tmp_path: Path, held_port: int
) -> None:
    config = load_config(write_config(tmp_path, port=held_port), {})
    assert await current_url(config) == f"http://127.0.0.1:{held_port}/mcp"
    assert await current_url(config, require_published=True) is None
    write_port(config.data_dir, held_port + 1)  # a file nobody owns
    assert await current_url(config) == f"http://127.0.0.1:{held_port}/mcp"
    assert await current_url(config, require_published=True) is None


async def test_a_local_bearer_uses_the_published_host_even_with_a_different_config_host(
    tmp_path: Path,
) -> None:
    config = replace(load_config(write_config(tmp_path, port=41000), {}), host="127.0.0.2")
    with DataDirLock(config.data_dir):
        write_port(config.data_dir, config.port)
        assert await current_url(config) == "http://127.0.0.2:41000/mcp"
        assert await current_url(config, require_published=True) is None

        write_port(config.data_dir, config.port, host="127.0.0.1")
        assert await current_url(config, require_published=True) == "http://127.0.0.1:41000/mcp"


async def test_a_local_bearer_is_not_sent_to_a_foreign_listener_on_a_fixed_port(
    tmp_path: Path, data_dir: Path
) -> None:
    requests: list[bytes] = []

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(http_reply("401 Unauthorized", ""))
        await writer.drain()
        writer.close()

    foreign = await asyncio.start_server(respond, "127.0.0.1", 0)
    async with foreign:
        port = int(foreign.sockets[0].getsockname()[1])
        config = write_config(tmp_path, port=port)
        TokenRegistry(data_dir).issue("demo", "orchestrator")
        proxy = await spawn_proxy("--config", str(config))
        assert "could not start" in await first_error(proxy, "modern")
    assert requests == []


async def test_a_port_of_zero_is_the_published_port_only_while_its_pid_owns_the_lock(
    tmp_path: Path,
) -> None:
    config = load_config(write_config(tmp_path), {})
    assert await current_url(config) is None, "nothing published"
    write_port(config.data_dir, 41000)  # names this process
    assert await current_url(config) is None, "this process does not hold the lock yet"
    with DataDirLock(config.data_dir):
        assert await current_url(config) == "http://127.0.0.1:41000/mcp"
        (config.data_dir / PORT_FILE_NAME).write_text('{"pid": 1, "port": 41000}', "utf-8")
        assert await current_url(config) is None, "the file names a pid that is not the owner"
    write_port(config.data_dir, 41000)
    assert await current_url(config) is None, "the lock is free again"


@pytest.mark.parametrize("pid", [10**12, 2**63, 2**200])
async def test_a_port_file_with_an_impossible_pid_is_ignored_not_fatal(
    tmp_path: Path, pid: int
) -> None:
    config = load_config(write_config(tmp_path), {})
    (config.data_dir).mkdir(parents=True)
    (config.data_dir / PORT_FILE_NAME).write_text(f'{{"pid": {pid}, "port": 41000}}', "utf-8")
    assert await current_url(config) is None


# --- refusals ------------------------------------------------------------------------------


@pytest.mark.parametrize("era", ERAS)
async def test_a_lock_held_by_a_server_at_another_address_is_reported(
    tmp_path: Path, data_dir: Path, held_port: int, era: str
) -> None:
    owner = write_config(tmp_path)
    server = start_plotroom("serve", "--config", str(owner))
    try:
        server.wait_for_log("serving")
        other = write_config(tmp_path, port=held_port, name="other")  # a fixed, dead address
        proxy = await spawn_proxy("--config", str(other), "--start-timeout", "3")
        try:
            proxy.send(first_request(era))
            reply = await proxy.answer(0)
        finally:
            await proxy.close()
        message = reply["error"]["message"]
        assert "another plotroom serve owns" in message
        assert f"pid {owner_pid(data_dir)}" in message
        assert is_locked(data_dir), "the owner is untouched"
    finally:
        server.stop()


async def first_error(proxy: RawProxy, era: str) -> str:
    """Send the era's first request and return the error message it was answered with."""
    try:
        proxy.send(first_request(era))
        reply = await proxy.answer(0)
    finally:
        await proxy.close()
    return str(reply["error"]["message"])


@pytest.mark.parametrize("era", ERAS)
async def test_a_daemon_that_cannot_start_is_reported_with_its_reason(
    tmp_path: Path, data_dir: Path, era: str
) -> None:
    path = tmp_path / "noprojects.toml"
    path.write_text(f'data_dir = "{data_dir}"\nport = 0\n', encoding="utf-8")
    message = await first_error(await spawn_proxy("--config", str(path)), era)
    assert "no projects configured" in message


@pytest.mark.parametrize("era", ERAS)
async def test_something_else_on_the_port_is_reported_and_not_proxied_to(
    tmp_path: Path, data_dir: Path, era: str
) -> None:
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        config = write_config(tmp_path, port=int(foreign.getsockname()[1]))
        message = await first_error(await spawn_proxy("--config", str(config)), era)
    assert "could not start" in message
    assert "address" in message.lower() or "bind" in message.lower()


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("how", ["flag", "environment"])
async def test_an_explicit_url_never_starts_a_daemon(
    tmp_path: Path, data_dir: Path, held_port: int, era: str, how: str
) -> None:
    url = f"http://127.0.0.1:{held_port}/mcp"
    config = write_config(tmp_path)
    if how == "flag":
        proxy = await spawn_proxy("--url", url, "--config", str(config))
    else:
        proxy = await spawn_proxy("--config", str(config), env={"PLOTROOM_URL": url})
    assert "cannot reach plotroom" in await first_error(proxy, era)
    assert not await asyncio.to_thread(data_dir.exists)


async def test_a_start_that_lost_the_lock_to_a_daemon_still_shutting_down_starts_again(
    tmp_path: Path, data_dir: Path
) -> None:
    """The lock outlives the listener during shutdown; once it is free, the start must retry."""
    path = write_config(tmp_path)
    config = load_config(path, {})
    old_owner = DataDirLock(data_dir)
    old_owner.acquire()
    starting = asyncio.ensure_future(ensure_daemon(config, path, env=child_env(), wait=WAIT))
    try:
        log = data_dir / LOG_FILE_NAME
        async with asyncio.timeout(WAIT):
            while "data directory is locked" not in await asyncio.to_thread(read_text, log):  # noqa: ASYNC110
                await asyncio.sleep(0.02)
        old_owner.release()
        await asyncio.wait_for(starting, timeout=WAIT)
    finally:
        old_owner.release()
        starting.cancel()
    assert is_locked(data_dir), "a second child took the directory once it was free"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def test_a_zero_or_nonsense_start_timeout_is_refused() -> None:
    for bad in ("0", "-3", "nan", "inf", "soon"):
        with pytest.raises(SystemExit) as stopped:
            main(["connect", "--stdio", "--start-timeout", bad])
        assert stopped.value.code == 2


def test_the_start_timeout_is_a_command_line_option() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "plotroom", "connect", "--help"],
        capture_output=True,
        text=True,
        env=child_env(),
        check=True,
    )
    assert "--start-timeout" in result.stdout


# --- the daemon's own rotating log ----------------------------------------------------------


def wait_for_published_port(data_dir: Path) -> None:
    """Return once a daemon has published its port, i.e. once it is serving."""
    deadline = time.monotonic() + WAIT
    while read_port(data_dir) is None:
        assert time.monotonic() < deadline, "the daemon published no port"
        time.sleep(0.02)


def daemon_log_files(data_dir: Path) -> list[str]:
    return sorted(entry.name for entry in data_dir.glob("*.log*"))


def log_files(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir() if entry.name.startswith("serve.log"))


def every_line_is_json(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        assert isinstance(entry, dict)
        entries.append(entry)  # pyright: ignore[reportUnknownArgumentType]
    return entries


@pytest.mark.parametrize("backups", [1, 2])
def test_a_serve_with_a_log_file_rotates_it_and_keeps_only_the_configured_backups(
    tmp_path: Path, backups: int
) -> None:
    config = write_config(tmp_path, log_max_bytes=300, log_backups=backups)
    log_dir = tmp_path / "logs"
    log = log_dir / "serve.log"
    child = start_plotroom("serve", "--config", str(config), "--log-file", str(log))
    try:
        wait_for_published_port(tmp_path / "data")
    finally:
        child.stop()
    assert log_files(log_dir) == ["serve.log", *(f"serve.log.{n}" for n in range(1, backups + 1))]
    for name in log_files(log_dir):
        every_line_is_json(log_dir / name)
    last = every_line_is_json(log)[-1]
    assert last["logger"] == "uvicorn.error"
    assert last["msg"].startswith("Finished server process"), "the shutdown line is the newest"
    assert child.lines.get(timeout=WAIT) is None, "nothing went to stdout"
    assert child.seen == []


def test_a_hand_started_serve_logs_json_to_stdout_and_writes_no_file(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    child = start_plotroom("serve", "--config", str(config))
    try:
        serving = child.wait_for_log("serving")
    finally:
        child.stop()
    assert serving["level"] == "info"
    data_dir = tmp_path / "data"
    assert not any(".log" in entry.name for entry in data_dir.iterdir())
    assert not (data_dir / LOG_FILE_NAME).exists()


def test_a_log_path_that_cannot_be_written_is_a_logged_error_and_exit_one(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    config = write_config(tmp_path)
    child = start_plotroom("serve", "--config", str(config), "--log-file", str(blocker / "s.log"))
    failure = child.wait_for_log("cannot open the log file")
    assert failure["level"] == "error"
    assert child.process.wait(timeout=WAIT) == 1
    assert not (tmp_path / "data" / LOCK_FILE_NAME).exists(), "it never took the data directory"


async def test_a_daemon_started_on_demand_logs_to_the_data_directory_with_the_default_limits(
    tmp_path: Path, data_dir: Path
) -> None:
    config = write_config(tmp_path)
    async with Client(proxy_params(config), mode="auto") as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    assert daemon_log_files(data_dir) == [LOG_FILE_NAME]
    [announced] = serving_lines(data_dir)
    assert announced["data_dir"] == str(data_dir)


async def test_the_rotation_settings_in_the_config_reach_a_daemon_started_on_demand(
    tmp_path: Path, data_dir: Path
) -> None:
    config = write_config(tmp_path, log_max_bytes=300, log_backups=1)
    async with Client(proxy_params(config), mode="auto") as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    await asyncio.to_thread(stop_owner, data_dir)  # nothing rolls the files while they are read
    assert daemon_log_files(data_dir) == [
        LOG_FILE_NAME,
        f"{LOG_FILE_NAME}.1",
    ]
    for name in (LOG_FILE_NAME, f"{LOG_FILE_NAME}.1"):
        every_line_is_json(data_dir / name)


async def test_the_rotation_settings_reach_the_daemon_from_the_environment(
    tmp_path: Path, data_dir: Path
) -> None:
    config = write_config(tmp_path)
    params = proxy_params(config)
    params.env = {
        **(params.env or {}),
        "PLOTROOM_LOG_MAX_BYTES": "300",
        "PLOTROOM_LOG_BACKUPS": "1",
    }
    async with Client(params, mode="auto") as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    await asyncio.to_thread(stop_owner, data_dir)  # nothing rolls the files while they are read
    assert (data_dir / f"{LOG_FILE_NAME}.1").exists()
    assert not (data_dir / f"{LOG_FILE_NAME}.2").exists()


@pytest.mark.parametrize("era", ERAS)
async def test_a_config_that_fails_to_load_leaves_its_reason_in_the_daemons_log_file(
    tmp_path: Path, data_dir: Path, era: str
) -> None:
    path = tmp_path / "noprojects.toml"
    path.write_text(f'data_dir = "{data_dir}"\nport = 0\n', encoding="utf-8")
    message = await first_error(await spawn_proxy("--config", str(path)), era)
    assert "no projects configured" in message
    entries = every_line_is_json(data_dir / LOG_FILE_NAME)
    assert [entry["msg"] for entry in entries if entry["level"] == "error"] == [
        "invalid configuration"
    ]
    assert "no projects configured" in entries[-1]["reason"]


def test_output_written_before_logging_is_set_up_stays_readable_in_the_log_file(
    tmp_path: Path,
) -> None:
    log = tmp_path / "data" / LOG_FILE_NAME
    script = "import sys; sys.stderr.write('early trouble\\n'); sys.stdout.write('early output\\n')"
    child = start_daemon([sys.executable, "-c", script], child_env(), tmp_path / "data", log)
    assert child.wait(timeout=WAIT) == 0
    assert log.read_text(encoding="utf-8").splitlines() == ["early trouble", "early output"]
    assert logged_reason(log, 0) == "early output"


def test_a_second_serve_that_loses_the_lock_never_writes_through_the_winners_log_file(
    tmp_path: Path,
) -> None:
    config = write_config(tmp_path)
    log = tmp_path / "logs" / "serve.log"
    winner = start_plotroom("serve", "--config", str(config), "--log-file", str(log))
    try:
        wait_for_published_port(tmp_path / "data")
        before = log.read_text(encoding="utf-8")
        loser = start_plotroom("serve", "--config", str(config), "--log-file", str(log))
        refusal = loser.wait_for_log("data directory is locked")
        assert refusal["level"] == "error"
        assert loser.process.wait(timeout=WAIT) == EXIT_LOCKED
        assert "data directory is locked" not in log.read_text(encoding="utf-8")
        assert log.read_text(encoding="utf-8").startswith(before)
    finally:
        winner.stop()
