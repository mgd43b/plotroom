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
from pathlib import Path
from typing import Any

import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import child_env, start_plotroom, tool_json
from plotroom.app.cli import main
from plotroom.app.config import load_config
from plotroom.app.daemon_start import (
    LOG_FILE_NAME,
    answers_ping,
    daemon_env,
    ensure_daemon,
    logged_reason,
    serve_command,
    speaks_mcp,
    start_daemon,
)
from plotroom.app.lock import LOCK_FILE_NAME, DataDirLock, LockError
from rawproxy import ERAS, RawProxy, first_request, spawn_proxy

MODES = ["legacy", "auto"]
WAIT = 60.0
EMPTY_BOARD_DATA: dict[str, Any] = {
    "project": "demo",
    "cursor": 0,
    "lanes": {},
    "open_items": {},
    "items": [],
    "left_out": 0,
}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def write_fixed_config(
    directory: Path, port: int, *, projects: tuple[str, ...] = ("demo",)
) -> Path:
    path = directory / f"config-{port}.toml"
    tables = "\n".join(f"[projects.{name}]" for name in projects)
    path.write_text(
        f'data_dir = "{directory / "data"}"\nhost = "127.0.0.1"\nport = {port}\n\n{tables}\n',
        encoding="utf-8",
    )
    return path


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
        (http_reply("500 Internal Server Error", PING_RESULT), False),
        (http_reply("200 OK", '{"jsonrpc":"2.0","id":"x","result":{}}'), False),
        (http_reply("200 OK", "<html>hello</html>", "text/html"), False),
    ],
)
async def test_the_probe_accepts_only_a_successful_ping_answer(
    reply: bytes, expected: bool
) -> None:
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
    config = write_fixed_config(tmp_path, free_port())
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
    config = write_fixed_config(tmp_path, free_port())
    async with Client(proxy_params(config), mode="auto") as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True


@pytest.mark.parametrize("mode", MODES)
async def test_a_running_daemon_is_used_and_none_is_started(
    tmp_path: Path, data_dir: Path, mode: str
) -> None:
    config = write_fixed_config(tmp_path, free_port())
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


async def test_two_clients_starting_at_once_share_one_daemon(
    tmp_path: Path, data_dir: Path
) -> None:
    config = write_fixed_config(tmp_path, free_port())

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
    config = write_fixed_config(tmp_path, free_port())
    async with Client(proxy_params(config), mode=mode) as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
        first = owner_pid(data_dir)
        stop_owner(data_dir)
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    assert owner_pid(data_dir) != first
    assert len(serving_lines(data_dir)) == 2


# --- refusals ------------------------------------------------------------------------------


@pytest.mark.parametrize("era", ERAS)
async def test_a_lock_held_by_a_server_at_another_address_is_reported(
    tmp_path: Path, data_dir: Path, era: str
) -> None:
    owner = write_fixed_config(tmp_path, free_port())
    server = start_plotroom("serve", "--config", str(owner))
    try:
        server.wait_for_log("serving")
        other = write_fixed_config(tmp_path, free_port())
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
    path.write_text(f'data_dir = "{data_dir}"\nport = {free_port()}\n', encoding="utf-8")
    message = await first_error(await spawn_proxy("--config", str(path)), era)
    assert "no projects configured" in message


@pytest.mark.parametrize("era", ERAS)
async def test_something_else_on_the_port_is_reported_and_not_proxied_to(
    tmp_path: Path, data_dir: Path, era: str
) -> None:
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        config = write_fixed_config(tmp_path, int(foreign.getsockname()[1]))
        message = await first_error(await spawn_proxy("--config", str(config)), era)
    assert "could not start" in message
    assert "address" in message.lower() or "bind" in message.lower()


@pytest.mark.parametrize("era", ERAS)
async def test_a_port_of_zero_names_nothing_to_start(tmp_path: Path, era: str) -> None:
    config = tmp_path / "zero.toml"
    config.write_text(f'data_dir = "{tmp_path / "data"}"\nport = 0\n[projects.demo]\n', "utf-8")
    message = await first_error(await spawn_proxy("--config", str(config)), era)
    assert "port is 0" in message


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("how", ["flag", "environment"])
async def test_an_explicit_url_never_starts_a_daemon(
    tmp_path: Path, data_dir: Path, era: str, how: str
) -> None:
    url = f"http://127.0.0.1:{free_port()}/mcp"
    config = write_fixed_config(tmp_path, free_port())
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
    path = write_fixed_config(tmp_path, free_port())
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
