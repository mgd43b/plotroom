"""On-demand daemon start for ``plotroom connect --stdio``: environment, races, refusals, restarts."""

from __future__ import annotations

import asyncio
import fcntl
import io
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from textwrap import dedent
from typing import Any

import httpx
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import child_env, start_plotroom, tool_json, write_config
from plotroom.adapters.mcp.proxy import LocalEndpoint, UpstreamStartError
from plotroom.app.cli import EXIT_LOCKED, main
from plotroom.app.config import load_config
from plotroom.app.daemon_start import (
    LOG_FILE_NAME,
    DaemonLocation,
    daemon_env,
    ensure_daemon,
    lock_owner,
    logged_reason,
    probe_status,
    serve_command,
    start_daemon,
)
from plotroom.app.lock import LOCK_FILE_NAME, DataDirLock, LockError
from plotroom.app.portfile import (
    PORT_FILE_NAME,
    directory_identity,
    prepare_local_socket,
    published_socket,
    read_listener,
    read_port,
    remove_local_socket,
    write_port,
)
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
    "claims": 0,
    "overlaps": 0,
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


@pytest.fixture
def make_local_socket() -> Iterator[Callable[[Path], Path]]:
    paths: list[Path] = []

    def make(directory: Path) -> Path:
        path = prepare_local_socket(directory)
        paths.append(path)
        return path

    yield make
    for path in paths:
        remove_local_socket(path)


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


async def answer_on_unix(
    path: Path, reply: bytes | None
) -> tuple[asyncio.Server, asyncio.Queue[bytes]]:
    requests: asyncio.Queue[bytes] = asyncio.Queue()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            requests.put_nowait(await reader.readuntil(b"\r\n\r\n"))
            if reply is None:
                await reader.read()  # keep waiting until the client's readiness timeout
            else:
                writer.write(reply)
                await writer.drain()
        except asyncio.IncompleteReadError as exc:
            requests.put_nowait(exc.partial)
        finally:
            writer.close()
            await writer.wait_closed()

    return await asyncio.start_unix_server(handle, path), requests


def http_reply(status: str, body: str, content_type: str = "application/json") -> bytes:
    payload = body.encode()
    head = f"HTTP/1.1 {status}\r\ncontent-type: {content_type}\r\ncontent-length: {len(payload)}"
    return f"{head}\r\nconnection: close\r\n\r\n".encode() + payload


@pytest.mark.parametrize(
    ("status", "matches", "expected"),
    [
        ("401 Unauthorized", True, True),
        ("401 Unauthorized", False, False),
        ("421 Misdirected Request", True, False),
        ("200 OK", True, False),
    ],
)
async def test_readiness_requires_a_401_from_the_verified_peer(
    make_local_socket: Callable[[Path], Path],
    tmp_path: Path,
    status: str,
    matches: bool,
    expected: bool,
) -> None:
    path = make_local_socket(tmp_path)
    server, requests = await answer_on_unix(path, http_reply(status, ""))
    endpoint = LocalEndpoint(
        "http://127.0.0.1:41000/mcp",
        "localhost:41000",
        str(path),
        os.getpid() if matches else os.getppid(),
    )
    async with server:
        assert (await probe_status(endpoint) == 401) is expected
        received = await asyncio.wait_for(requests.get(), WAIT)
    if matches:
        assert received.startswith(b"HEAD /mcp HTTP/1.1\r\n")
        assert b"\r\nhost: localhost:41000\r\n" in received.lower()
        assert b"authorization:" not in received.lower()
        assert b"content-length:" not in received.lower()
    else:
        assert received == b""


async def test_the_verified_probe_is_bounded_when_the_peer_stalls(
    make_local_socket: Callable[[Path], Path], tmp_path: Path
) -> None:
    path = make_local_socket(tmp_path)
    server, requests = await answer_on_unix(path, None)
    endpoint = LocalEndpoint(
        "http://127.0.0.1:41000/mcp", "localhost:41000", str(path), os.getpid()
    )
    async with server:
        async with asyncio.timeout(5):
            assert await probe_status(endpoint, within=0.3) is None
            assert (await requests.get()).startswith(b"HEAD /mcp ")


async def test_a_reconnect_sends_no_http_to_a_different_peer_at_the_same_path(
    make_local_socket: Callable[[Path], Path],
    tmp_path: Path,
) -> None:
    path = make_local_socket(tmp_path)
    endpoint = LocalEndpoint(
        "http://127.0.0.1:41000/mcp", "localhost:41000", str(path), os.getpid()
    )
    server, _ = await answer_on_unix(path, http_reply("401 Unauthorized", ""))
    async with server:
        assert await probe_status(endpoint) == 401
    script = dedent("""\
        import socket
        import sys

        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(sys.argv[1])
            listener.listen()
            print("ready", flush=True)
            conn, _ = listener.accept()
            with conn:
                print(len(conn.recv(4096)), flush=True)
        """)
    foreign = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(path),
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        assert foreign.stdout is not None
        async with asyncio.timeout(WAIT):
            assert await foreign.stdout.readline() == b"ready\n"
            with pytest.raises(httpx.ConnectError, match="identity could not be verified"):
                async with endpoint.stream(
                    "POST",
                    content=b'{"jsonrpc":"2.0","method":"ping","id":1}',
                    headers={"authorization": "Bearer fixture"},
                ):
                    pytest.fail("the foreign peer was accepted")
            assert await foreign.stdout.readline() == b"0\n"
            assert await foreign.wait() == 0
    finally:
        if foreign.returncode is None:
            foreign.kill()
            await foreign.wait()
        path.unlink(missing_ok=True)


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


def test_config_and_alias_changes_after_token_selection_keep_the_same_instance(
    tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = TokenRegistry(data_dir).issue("demo", "orchestrator")
    other_dir = tmp_path / "other"
    TokenRegistry(other_dir).issue("demo", "clerk")
    alias = tmp_path / "alias"
    alias.symlink_to(data_dir, target_is_directory=True)
    path = tmp_path / "config.toml"
    path.write_text('data_dir = "alias"\nport = 0\n[projects.demo]\n', encoding="utf-8")

    def select_then_edit(directory: Path, token_id: str | None) -> str | None:
        assert directory == data_dir.resolve()
        selected = select_local_token(directory, token_id)
        assert selected == first.bearer
        alias.unlink()
        alias.symlink_to(other_dir, target_is_directory=True)
        path.write_text('data_dir = "other"\nport = 0\n[projects.demo]\n', encoding="utf-8")
        return selected

    stdin = io.TextIOWrapper(io.BytesIO(json.dumps(first_request("modern")).encode() + b"\n"))
    result = io.BytesIO()
    stdout = io.TextIOWrapper(result)
    with monkeypatch.context() as patch:
        patch.setattr("plotroom.app.cli.select_local_token", select_then_edit)
        patch.setattr(sys, "stdin", stdin)
        patch.setattr(sys, "stdout", stdout)
        assert main(["connect", "--stdio", "--config", str(path)]) == 0
    response = json.loads(result.getvalue())
    assert response["result"]["isError"] is False
    assert is_locked(data_dir)
    assert not (other_dir / LOCK_FILE_NAME).exists()
    assert not (other_dir / PORT_FILE_NAME).exists()


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
    async with httpx.AsyncClient(trust_env=False) as http:
        assert (await http.head(f"http://127.0.0.1:{port}/mcp")).status_code == 401


async def test_a_killed_daemons_file_is_not_trusted_and_a_new_client_reaches_the_new_daemon(
    tmp_path: Path, data_dir: Path
) -> None:
    config = write_config(tmp_path)
    async with Client(proxy_params(config), mode="auto") as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    first, first_port = published(data_dir)
    first_listener = read_listener(data_dir)
    assert first_listener is not None
    assert first_listener.local_socket is not None
    first_socket = Path(first_listener.local_socket)
    os.kill(first, signal.SIGKILL)
    await asyncio.to_thread(wait_until_unlocked, data_dir)
    assert published(data_dir) == (first, first_port), "a killed daemon leaves its file behind"
    async with Client(proxy_params(config), mode="auto") as client:
        assert tool_json(await client.call_tool("snapshot", {}))["ok"] is True
    second, second_port = published(data_dir)
    assert second == owner_pid(data_dir) != first
    assert len(serving_lines(data_dir)) == 2
    assert not first_socket.parent.exists(), "the successor removed the stale socket directory"
    async with httpx.AsyncClient(trust_env=False) as http:
        assert (await http.head(f"http://127.0.0.1:{second_port}/mcp")).status_code == 401


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


@pytest.mark.parametrize(
    ("server_host", "alias", "client_pattern", "server_pattern"),
    [
        ("localhost", "localhost", "localhost:*", "localhost:*"),
        ("127.0.0.1", "LOCALHOST", "localhost", "localhost"),
        ("127.0.0.1", "127.0.0.2", "127.0.0.2:*", "127.0.0.2:*"),
        ("127.0.0.1", "::1", "[::1]:*", "[::1]:*"),
        ("127.0.0.1", "127.0.0.2", "127.0.0.2:*", "127.0.0.1:*"),
        ("localhost", "localhost", "127.0.0.1:*", "localhost:*"),
    ],
)
async def test_loopback_host_aliases_preserve_or_fall_back_to_an_allowed_host(
    tmp_path: Path,
    data_dir: Path,
    server_host: str,
    alias: str,
    client_pattern: str,
    server_pattern: str,
) -> None:
    shared = 'data_dir = "data"\nport = 0\n[projects.demo]\n'
    server_path = tmp_path / "serve.toml"
    server_path.write_text(
        f'host = "{server_host}"\nallowed_hosts = ["{server_pattern}"]\n{shared}',
        encoding="utf-8",
    )
    client_path = tmp_path / "connect.toml"
    client_path.write_text(
        f'host = "{alias}"\nallowed_hosts = ["{client_pattern}"]\n{shared}',
        encoding="utf-8",
    )
    server = start_plotroom("serve", "--config", str(server_path))
    try:
        server.wait_for_log("serving")
        async with Client(proxy_params(client_path), mode="auto") as client:
            assert tool_json(await client.call_tool("snapshot", {}))["data"]["project"] == "demo"
        assert not (data_dir / LOG_FILE_NAME).exists()
    finally:
        server.stop()


# --- reading the port back ------------------------------------------------------------------


def test_lock_owner_reads_a_stale_live_pid_without_taking_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lock_file = tmp_path / LOCK_FILE_NAME
    pid = os.getpid()
    contents = f"{pid}\n"
    lock_file.write_text(contents, encoding="utf-8")

    def unexpected_flock(*args: object) -> None:
        pytest.fail("the lock owner probe attempted a kernel lock operation")

    # Guard the syscall itself so a short acquire/release cannot go unnoticed.
    monkeypatch.setattr(fcntl, "flock", unexpected_flock)
    assert lock_owner(tmp_path) == pid
    assert lock_file.read_text(encoding="utf-8") == contents
    assert "locked" not in caplog.text


def test_lock_owner_does_not_create_a_missing_lock_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    assert lock_owner(data_dir) is None
    assert not data_dir.exists()


def test_lock_owner_does_not_wait_for_a_fifo_writer(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / LOCK_FILE_NAME)
    program = (
        "import sys\n"
        "from pathlib import Path\n"
        "from plotroom.app.daemon_start import lock_owner\n"
        "if lock_owner(Path(sys.argv[1])) is not None:\n"
        "    raise SystemExit('a named pipe reported a PID')\n"
    )
    subprocess.run([sys.executable, "-c", program, str(tmp_path)], check=True, timeout=WAIT)


def test_lock_owner_ignores_an_oversized_record_with_a_live_pid(tmp_path: Path) -> None:
    lock_file = tmp_path / LOCK_FILE_NAME
    lock_file.write_text(f"{os.getpid()}" + " " * 64, encoding="utf-8")
    assert lock_owner(tmp_path) is None


@pytest.mark.parametrize("contents", [b"", b"not a PID", b"\xff", b"0", b"-1", b"1000000000000"])
def test_lock_owner_ignores_invalid_pids(tmp_path: Path, contents: bytes) -> None:
    (tmp_path / LOCK_FILE_NAME).write_bytes(contents)
    assert lock_owner(tmp_path) is None


@pytest.mark.parametrize("error", [ProcessLookupError, PermissionError])
def test_lock_owner_checks_pid_liveness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[OSError]
) -> None:
    pid = os.getpid()
    (tmp_path / LOCK_FILE_NAME).write_text(str(pid), encoding="utf-8")

    def check_pid(checked_pid: int, sig: int) -> None:
        assert (checked_pid, sig) == (pid, 0)
        raise error

    monkeypatch.setattr(os, "kill", check_pid)
    assert lock_owner(tmp_path) == (pid if error is PermissionError else None)


async def test_a_fixed_port_requires_a_matching_publication(
    make_local_socket: Callable[[Path], Path], tmp_path: Path, held_port: int
) -> None:
    config = load_config(write_config(tmp_path, port=held_port), {})
    assert await DaemonLocation(config).current_endpoint() is None
    with DataDirLock(config.data_dir):
        path = make_local_socket(config.data_dir)
        location = DaemonLocation(config)
        write_port(
            config.data_dir,
            65535 if held_port != 65535 else 65534,
            host="127.0.0.1",
            local_socket=path,
        )
        assert await location.current_endpoint() is None
        write_port(config.data_dir, held_port, host="127.0.0.1", local_socket=path)
        assert await location.current_endpoint() == LocalEndpoint(
            config.url, f"127.0.0.1:{held_port}", str(path), os.getpid()
        )


async def test_a_local_bearer_uses_the_published_host_even_with_a_different_config_host(
    make_local_socket: Callable[[Path], Path],
    tmp_path: Path,
) -> None:
    config = replace(load_config(write_config(tmp_path, port=41000), {}), host="127.0.0.2")
    with DataDirLock(config.data_dir):
        path = make_local_socket(config.data_dir)
        location = DaemonLocation(config)
        write_port(config.data_dir, config.port, host="127.0.0.1")
        assert await location.current_endpoint() is None
        assert await location.requires_upgrade()

        write_port(config.data_dir, config.port, host="127.0.0.1", local_socket=path)
        assert await location.current_endpoint() == LocalEndpoint(
            "http://127.0.0.1:41000/mcp", "127.0.0.1:41000", str(path), os.getpid()
        )
    assert await location.current_endpoint() is None


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


@pytest.mark.parametrize(
    ("private_unavailable", "message"),
    [
        (False, "restart the running plotroom serve"),
        (True, "the running plotroom serve has no private local listener"),
    ],
)
async def test_a_running_daemon_without_a_private_socket_receives_no_probe(
    tmp_path: Path, data_dir: Path, private_unavailable: bool, message: str
) -> None:
    requests: list[bytes] = []

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        requests.append(await reader.read(4096))
        writer.close()
        await writer.wait_closed()

    foreign = await asyncio.start_server(respond, "127.0.0.1", 0)
    async with foreign, asyncio.timeout(WAIT):
        port = int(foreign.sockets[0].getsockname()[1])
        config = write_config(tmp_path, port=port)
        TokenRegistry(data_dir).issue("demo", "orchestrator")
        with DataDirLock(data_dir):
            write_port(data_dir, port, host="127.0.0.1", local_unavailable=private_unavailable)
            proxy = await spawn_proxy("--config", str(config))
            assert message in await first_error(proxy, "modern")
    assert requests == []
    assert not (data_dir / LOG_FILE_NAME).exists()


async def test_a_port_of_zero_is_the_published_port_only_while_its_pid_owns_the_lock(
    make_local_socket: Callable[[Path], Path],
    tmp_path: Path,
) -> None:
    config = load_config(write_config(tmp_path), {})
    config.data_dir.mkdir()
    location = DaemonLocation(config)
    assert await location.current_endpoint() is None, "nothing published"
    path = make_local_socket(config.data_dir)
    write_port(config.data_dir, 41000, host="127.0.0.1", local_socket=path)
    assert await location.current_endpoint() is None, "this process does not hold the lock yet"
    with DataDirLock(config.data_dir):
        endpoint = await location.current_endpoint()
        assert endpoint == LocalEndpoint(
            "http://127.0.0.1:41000/mcp", "127.0.0.1:41000", str(path), os.getpid()
        )
        fields = {
            "pid": os.getppid(),
            "port": 41000,
            "host": "127.0.0.1",
            "local_socket": str(path),
        }
        (config.data_dir / PORT_FILE_NAME).write_text(json.dumps(fields), "utf-8")
        assert await location.current_endpoint() is None, (
            "the file names a pid that is not the owner"
        )
        write_port(config.data_dir, 41000, host="127.0.0.1", local_socket=path)
    assert await location.current_endpoint() is None, "the lock is free again"


async def test_a_replaced_data_directory_is_never_used_by_the_pinned_proxy(
    make_local_socket: Callable[[Path], Path], tmp_path: Path
) -> None:
    path = write_config(tmp_path)
    config = load_config(path, {})
    config.data_dir.mkdir()
    location = DaemonLocation(config)
    config.data_dir.rename(tmp_path / "previous")
    with DataDirLock(config.data_dir):
        socket_path = make_local_socket(config.data_dir)
        write_port(config.data_dir, 41000, host="127.0.0.1", local_socket=socket_path)
        assert await location.current_endpoint() is None
        with pytest.raises(UpstreamStartError, match="canonical plotroom data directory changed"):
            await ensure_daemon(location, path, env=child_env())
    assert not (config.data_dir / LOG_FILE_NAME).exists()


async def test_the_child_refuses_a_directory_replaced_during_launch(
    tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_config(tmp_path)
    location = DaemonLocation(load_config(config_path, {}))

    def replace_then_start(
        command: Sequence[str], env: Mapping[str, str], cwd: Path, log_path: Path
    ) -> subprocess.Popen[bytes]:
        cwd.rename(tmp_path / "previous")
        cwd.mkdir()
        return start_daemon(command, env, cwd, log_path)

    monkeypatch.setattr("plotroom.app.daemon_start.start_daemon", replace_then_start)
    with pytest.raises(UpstreamStartError, match="canonical plotroom data directory changed"):
        await ensure_daemon(location, config_path, env=child_env(), wait=WAIT)
    assert not (data_dir / PORT_FILE_NAME).exists()
    assert not (data_dir / LOCK_FILE_NAME).exists()


async def test_a_published_socket_in_a_nonprivate_directory_is_ignored(
    tmp_path: Path,
    make_local_socket: Callable[[Path], Path],
) -> None:
    config = load_config(write_config(tmp_path), {})
    with DataDirLock(config.data_dir):
        path = make_local_socket(config.data_dir)
        path.parent.chmod(0o755)
        write_port(config.data_dir, 41000, host="127.0.0.1", local_socket=path)
        assert await DaemonLocation(config).current_endpoint() is None


def test_each_socket_attempt_uses_a_fresh_private_directory(
    tmp_path: Path,
    make_local_socket: Callable[[Path], Path],
) -> None:
    first, second = make_local_socket(tmp_path), make_local_socket(tmp_path)
    assert first.parent != second.parent
    assert first.name == second.name
    assert first.parent.stat().st_mode & 0o777 == 0o700
    assert second.parent.stat().st_mode & 0o777 == 0o700


def test_a_short_shared_data_directory_works_across_different_temporary_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with tempfile.TemporaryDirectory(prefix="plotroom-test-", dir="/tmp") as shared:
        data_dir = Path(shared)
        path = prepare_local_socket(data_dir)
        try:
            assert path.parent.parent == data_dir
            monkeypatch.setattr("plotroom.app.portfile._SOCKET_ROOT", tmp_path / "private-tmp")
            assert published_socket(data_dir, directory_identity(data_dir), str(path)) == path
        finally:
            remove_local_socket(path)


@pytest.mark.parametrize("pid", [10**12, 2**63, 2**200])
async def test_a_port_file_with_an_impossible_pid_is_ignored_not_fatal(
    tmp_path: Path, pid: int
) -> None:
    config = load_config(write_config(tmp_path), {})
    (config.data_dir).mkdir(parents=True)
    (config.data_dir / PORT_FILE_NAME).write_text(f'{{"pid": {pid}, "port": 41000}}', "utf-8")
    assert await DaemonLocation(config).current_endpoint() is None


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
@pytest.mark.parametrize("nonregular_publication", [False, True])
async def test_something_else_on_the_port_is_reported_and_not_proxied_to(
    tmp_path: Path, data_dir: Path, era: str, nonregular_publication: bool
) -> None:
    if nonregular_publication:
        await asyncio.to_thread(data_dir.mkdir)
        os.mkfifo(data_dir / PORT_FILE_NAME)
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
    starting = asyncio.ensure_future(
        ensure_daemon(DaemonLocation(config), path, env=child_env(), wait=WAIT)
    )
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
