"""Start ``plotroom serve`` on demand for ``plotroom connect --stdio`` (AD-3).

A server that answers at the config's URL, not the lock, says whether a daemon runs. A second
``serve`` is harmless (it exits with the lock code), so two clients starting at once need no
probe: whoever loses the lock waits for the winner to answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import httpx

from plotroom.adapters.mcp.proxy import UpstreamStartError
from plotroom.app.cli import DEFAULT_START_TIMEOUT, EXIT_LOCKED
from plotroom.app.config import Config
from plotroom.app.lock import DataDirLock, LockError

log = logging.getLogger("plotroom.daemon_start")

LOG_FILE_NAME = "plotroom-serve.log"
_POLL_SECONDS = 0.05
_PROBE_TIMEOUT = 2.0

# What a daemon started for a client inherits: enough to run, nothing of the client's own
# environment (its tokens, its session variables). The PLOTROOM_* names are the config loader's
# closed override list, so the daemon resolves the same settings the proxy did.
_RUNTIME_KEYS = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ")
_CONFIG_KEYS = (
    "PLOTROOM_CONFIG",
    "PLOTROOM_DATA_DIR",
    "PLOTROOM_HOST",
    "PLOTROOM_PORT",
    "PLOTROOM_ALLOWED_HOSTS",
    "PLOTROOM_PROJECTS",
)
_PATH_KEYS = ("PLOTROOM_CONFIG", "PLOTROOM_DATA_DIR")


def daemon_env(env: Mapping[str, str]) -> dict[str, str]:
    """The environment a daemon started on demand runs with."""
    clean = {key: env[key] for key in _RUNTIME_KEYS if key in env}
    for key in _CONFIG_KEYS:
        if key in env:
            # relative paths mean the client's working directory, not the daemon's
            clean[key] = (
                str(Path(env[key]).expanduser().resolve()) if key in _PATH_KEYS else env[key]
            )
    return clean


def serve_command(config_path: Path | None) -> list[str]:
    command = [sys.executable, "-m", "plotroom", "serve"]
    if config_path is not None:
        command += ["--config", str(config_path.expanduser().resolve())]
    return command


def start_daemon(
    command: Sequence[str], env: Mapping[str, str], cwd: Path, log_path: Path
) -> subprocess.Popen[bytes]:
    """Run ``command`` detached from the client: its own session, no stdin, output to a file."""
    cwd.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as out:
        return subprocess.Popen(  # noqa: S603 - the command is this interpreter running plotroom
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            env=dict(env),
            cwd=cwd,
            start_new_session=True,
            close_fds=True,
        )


async def speaks_mcp(url: str) -> bool:
    """Whether something at ``url`` answers a JSON-RPC ``ping``: an MCP server, not any program.

    A bare TCP connect would take any program on the port for the daemon.
    """
    ping = {"jsonrpc": "2.0", "id": "probe", "method": "ping"}
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    try:
        async with (
            httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as http,
            http.stream("POST", url, json=ping, headers=headers) as response,
        ):
            first = b""
            async for chunk in response.aiter_bytes():
                first += chunk
                if b"jsonrpc" in first or len(first) > 4096:
                    break
    except httpx.HTTPError, OSError:
        return False
    return b"jsonrpc" in first


def logged_reason(log_path: Path, since: int) -> str | None:
    """Why the daemon failed, from what it wrote to ``log_path`` after byte offset ``since``.

    The last error it logged, with the last line of its traceback when it logged one; failing that,
    the last line of output that is not a log line (an import error, a bare traceback).
    """
    try:
        with log_path.open("rb") as handle:
            handle.seek(since)
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    reason: str | None = None
    plain: str | None = None
    for line in text.splitlines():
        try:
            entry: Any = json.loads(line)
        except ValueError:
            plain = line.strip() or plain
            continue
        if not isinstance(entry, dict):
            continue
        fields = cast("dict[str, Any]", entry)
        if fields.get("level") in ("error", "critical"):
            parts = [str(fields[key]) for key in ("msg", "reason") if fields.get(key)]
            trace = str(fields.get("exc") or "").strip().splitlines()
            if trace:
                parts.append(trace[-1])
            reason = ": ".join(parts) or reason
    found = reason or plain
    return found[:400] if found else None


def holds_lock(data_dir: Path) -> bool:
    """Whether a server owns ``data_dir`` right now."""
    probe = DataDirLock(data_dir)
    try:
        probe.acquire()
    except LockError:
        return True
    probe.release()
    return False


async def ensure_daemon(
    config: Config,
    config_path: Path | None,
    *,
    env: Mapping[str, str],
    wait: float = DEFAULT_START_TIMEOUT,
    command: Sequence[str] | None = None,
) -> None:
    """Return once something listens at the config's URL, starting ``plotroom serve`` if needed.

    Raises ``UpstreamStartError`` with a reason fit for the client when it cannot.
    """
    if config.port == 0:
        raise UpstreamStartError(
            "the config's port is 0, which names no address to start a server on; set a port"
        )
    if await speaks_mcp(config.url):
        return
    log_path = config.data_dir / LOG_FILE_NAME
    argv = list(command) if command is not None else serve_command(config_path)
    deadline = time.monotonic() + wait
    reason: str | None = None
    locked_out = False
    while True:
        offset = log_path.stat().st_size if log_path.exists() else 0
        try:
            child = await asyncio.to_thread(
                start_daemon, argv, daemon_env(env), config.data_dir, log_path
            )
        except OSError as exc:
            raise UpstreamStartError(f"cannot start plotroom serve: {exc.strerror or exc}") from exc
        log.info(
            "started plotroom serve", extra={"pid": child.pid, "data_dir": str(config.data_dir)}
        )
        # Wait on this child. When it loses the lock it waits for the winner to listen, and starts
        # again only if that winner went away (a daemon still shutting down keeps the lock a while).
        while time.monotonic() < deadline:
            if await speaks_mcp(config.url):
                return
            code = child.poll()
            if code is not None:
                reason = logged_reason(log_path, offset)
                if code != EXIT_LOCKED:
                    raise UpstreamStartError(
                        f"plotroom serve could not start: {reason or f'exited with code {code}'}"
                    )
                locked_out = True
                if not await asyncio.to_thread(holds_lock, config.data_dir):
                    break  # the owner is gone: try again
            await asyncio.sleep(_POLL_SECONDS)
        else:
            break
    if locked_out and reason:
        raise UpstreamStartError(
            f"{reason}, and nothing answers at {config.url}; "
            "that server uses another address than this config's"
        )
    raise UpstreamStartError(
        f"plotroom serve did not start listening at {config.url} within {wait:g}s"
        + (f" ({reason})" if reason else "")
    )
