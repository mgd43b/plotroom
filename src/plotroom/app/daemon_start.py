"""Start ``plotroom serve`` on demand for ``plotroom connect --stdio`` (AD-3).

The daemon publishes its listeners in the data directory. Local readiness requires HTTP 401 on a
Unix connection to the verified lock owner. A second ``serve`` exits with the lock code, and the
client that started it waits for the lock's owner to answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from plotroom.adapters.mcp.proxy import LocalEndpoint, UpstreamStartError
from plotroom.adapters.mcp.server import allowed_host
from plotroom.app.cli import DEFAULT_START_TIMEOUT, EXIT_LOCKED
from plotroom.app.config import Config
from plotroom.app.lock import LOCK_FILE_NAME, DataDirLock, LockError
from plotroom.app.portfile import (
    START_DIRECTORY_ENV,
    DirectoryIdentity,
    PublishedListener,
    directory_identity,
    directory_key,
    local_socket_path,
    private_socket_directory,
    read_listener,
)

log = logging.getLogger("plotroom.daemon_start")

LOG_FILE_NAME = "plotroom-serve.log"
_POLL_SECONDS = 0.05
_PROBE_TIMEOUT = 2.0
_MAX_PID_RECORD_BYTES = 64

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
    "PLOTROOM_LOG_MAX_BYTES",
    "PLOTROOM_LOG_BACKUPS",
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


def serve_command(config_path: Path | None, log_path: Path | None = None) -> list[str]:
    # -P: the daemon runs in the data directory, which must not shadow the installed package
    command = [sys.executable, "-P", "-m", "plotroom", "serve"]
    if log_path is not None:
        # The daemon rotates its own log; it runs in the data directory, so the path is absolute.
        command += ["--log-file", str(log_path.expanduser().resolve())]
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


async def endpoint_ready(endpoint: LocalEndpoint, *, within: float = _PROBE_TIMEOUT) -> bool:
    """The verified peer accepts the configured Host and requires authentication."""
    if within <= 0:
        return False
    try:
        async with (
            asyncio.timeout(min(within, _PROBE_TIMEOUT)),
            endpoint.stream("HEAD") as response,
        ):
            return response.status_code == 401
    except httpx.HTTPError, OSError, TimeoutError:
        return False


def logged_reason(log_path: Path, since: int) -> str | None:
    """Why the daemon failed, from what it wrote to ``log_path`` after byte offset ``since``.

    The last error it logged, with the last line of its traceback when it logged one; failing that,
    the last line of output that is not a log line (an import error, a bare traceback).
    """
    try:
        with log_path.open("rb") as handle:
            # a file smaller than the offset was rolled since: all of it is newer
            handle.seek(since if since <= os.fstat(handle.fileno()).st_size else 0)
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


def lock_owner(data_dir: Path) -> int | None:
    """Read the recorded PID if it is live, even when the file is stale."""
    try:
        with open(
            data_dir / LOCK_FILE_NAME,
            "rb",
            opener=lambda path, flags: os.open(path, flags | os.O_NONBLOCK),
        ) as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            record = handle.read(_MAX_PID_RECORD_BYTES + 1)
        if len(record) > _MAX_PID_RECORD_BYTES:
            return None
        pid = int(record.decode("utf-8"))
    except OSError, ValueError:
        return None
    return pid if pid > 0 and _pid_alive(pid) else None


def holds_lock(data_dir: Path) -> bool:
    """Whether a server owns ``data_dir`` right now."""
    probe = DataDirLock(data_dir)
    try:
        probe.acquire()
    except LockError:
        return True
    probe.release()
    return False


def _lock_still_held_by(data_dir: Path, pid: int) -> bool:
    """Check the kernel lock and confirm the recorded PID after the check."""
    return holds_lock(data_dir) and lock_owner(data_dir) == pid


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:  # the process exists but belongs to someone else
        return True
    except OSError, OverflowError:
        return False
    return True


class DaemonLocation:
    """One canonical data directory shared by token selection, startup, and listener discovery."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.identity = self._identify()

    def _identify(self) -> DirectoryIdentity | None:
        directory = self.config.data_dir
        if directory.resolve() != directory:
            raise UpstreamStartError("the canonical plotroom data directory changed")
        try:
            return directory_identity(directory)
        except FileNotFoundError:
            return None

    def prepare(self) -> DirectoryIdentity:
        current = self._identify()
        if self.identity is None:
            if current is None:
                self.config.data_dir.mkdir(parents=True, exist_ok=True)
                current = self._identify()
            self.identity = current
        if current is None or current != self.identity:
            raise UpstreamStartError("the canonical plotroom data directory changed")
        return current

    def _publication(self) -> PublishedListener | None:
        directory = self.config.data_dir
        if self.identity is None or self._identify() != self.identity:
            return None
        published = read_listener(directory)
        if published is None or self.config.port not in (0, published.port):
            return None
        # Both records may survive a crash. Confirm the recorded PID and the kernel lock, then
        # make sure publication still comes from the directory selected by the proxy.
        if lock_owner(directory) != published.pid or not _lock_still_held_by(
            directory, published.pid
        ):
            return None
        return published if self._identify() == self.identity else None

    def _current_endpoint(self) -> LocalEndpoint | None:
        published = self._publication()
        if published is None or published.host is None or self.identity is None:
            return None
        socket_path = local_socket_path(self.identity)
        if published.local_socket != str(socket_path) or not private_socket_directory(
            socket_path.parent
        ):
            return None
        config = replace(self.config, port=published.port)
        url = replace(config, host=published.host).url
        published_host = urlsplit(url).netloc
        alias = urlsplit(config.url).netloc.lower()
        candidates = (
            alias,
            published_host,
            alias.rsplit(":", 1)[0],
            published_host.rsplit(":", 1)[0],
        )
        host = next(
            (name for name in candidates if allowed_host(name, config.allowed_hosts)),
            published_host,
        )
        return LocalEndpoint(url, host, str(socket_path), published.pid)

    async def current_endpoint(self) -> LocalEndpoint | None:
        return await asyncio.to_thread(self._current_endpoint)

    async def requires_upgrade(self) -> bool:
        published = await asyncio.to_thread(self._publication)
        return published is not None and published.local_socket is None


async def _ready_endpoint(location: DaemonLocation, within: float) -> LocalEndpoint | None:
    endpoint = await location.current_endpoint()
    if endpoint is None or not await endpoint_ready(endpoint, within=within):
        return None
    return endpoint if endpoint == await location.current_endpoint() else None


async def ensure_daemon(
    location: DaemonLocation,
    config_path: Path | None,
    *,
    env: Mapping[str, str],
    wait: float = DEFAULT_START_TIMEOUT,
    command: Sequence[str] | None = None,
) -> LocalEndpoint:
    """Find or start the daemon for the pinned data directory and verify its ready listener."""
    identity = await asyncio.to_thread(location.prepare)
    endpoint = await _ready_endpoint(location, _PROBE_TIMEOUT)
    if endpoint is not None:
        return endpoint
    if await location.requires_upgrade():
        raise UpstreamStartError(
            "restart the running plotroom serve to enable verified local stdio"
        )
    config = location.config
    log_path = config.data_dir / LOG_FILE_NAME
    argv = list(command) if command is not None else serve_command(config_path, log_path)
    # The child rereads the config from its own cwd. Pin the instance even if the file changes.
    child_env = {
        **daemon_env(env),
        "PLOTROOM_DATA_DIR": str(config.data_dir),
        START_DIRECTORY_ENV: directory_key(identity),
    }
    deadline = time.monotonic() + wait
    reason: str | None = None
    while True:
        await asyncio.to_thread(location.prepare)
        offset = log_path.stat().st_size if log_path.exists() else 0
        try:
            child = await asyncio.to_thread(
                start_daemon, argv, child_env, config.data_dir, log_path
            )
        except OSError as exc:
            raise UpstreamStartError(f"cannot start plotroom serve: {exc.strerror or exc}") from exc
        log.info(
            "started plotroom serve", extra={"pid": child.pid, "data_dir": str(config.data_dir)}
        )
        exited = False
        # Wait on this child. When it loses the lock it waits for the winner to listen, and starts
        # again only if that winner went away (a daemon still shutting down keeps the lock a while).
        while (remaining := deadline - time.monotonic()) > 0:
            endpoint = await _ready_endpoint(location, remaining)
            if endpoint is not None:
                return endpoint
            code = child.poll()
            if code is not None:
                if not exited:  # read the log once per child, off the event loop
                    exited = True
                    reason = await asyncio.to_thread(logged_reason, log_path, offset)
                    if code != EXIT_LOCKED:
                        why = reason or f"exited with code {code}"
                        raise UpstreamStartError(f"plotroom serve could not start: {why}")
                if not await asyncio.to_thread(holds_lock, config.data_dir):
                    break  # the owner is gone: try again
            await asyncio.sleep(_POLL_SECONDS)
        else:
            break
    owner = await asyncio.to_thread(lock_owner, config.data_dir)
    endpoint = await location.current_endpoint()
    if await location.requires_upgrade():
        raise UpstreamStartError(
            "restart the running plotroom serve to enable verified local stdio"
        )
    url = endpoint.url if endpoint is not None else None
    if (
        owner is not None
        and owner != child.pid
        and await asyncio.to_thread(_lock_still_held_by, config.data_dir, owner)
    ):
        if url is None:
            answer = "it has published no address that answers"
        elif config.port == 0:
            answer = f"nothing answers at {url}, the address it published"
        else:
            answer = (
                f"nothing answers at {url}; that server uses another address than this config's"
            )
        raise UpstreamStartError(
            f"another plotroom serve owns {config.data_dir} (pid {owner}), and {answer}"
        )
    raise UpstreamStartError(
        f"plotroom serve did not start listening{f' at {url}' if url else ''} within {wait:g}s"
        + (f" (earlier: {reason})" if reason else "")
    )
