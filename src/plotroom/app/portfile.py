"""The port file: where a daemon publishes the address it actually bound (AD-3).

A client trusts the file only while the data-directory lock is held by the pid the file names
(see ``daemon_start``); the file alone proves nothing, since a killed daemon leaves it behind.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import stat
import tempfile
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

PORT_FILE_NAME = "plotroom.port"
START_DIRECTORY_ENV = "_PLOTROOM_START_DIRECTORY"
_MAX_PORT = 65535
_SOCKET_ROOT = Path("/tmp")  # noqa: S108 - use a short path and create a private directory

type DirectoryIdentity = tuple[int, int]


@dataclass(frozen=True)
class PublishedListener:
    pid: int
    port: int
    host: str | None
    local_socket: str | None = None
    local_unavailable: bool = False


@contextlib.contextmanager
def pin_directory(data_dir: Path) -> Generator[int]:
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def directory_identity(data_dir: Path | int) -> DirectoryIdentity:
    info = os.fstat(data_dir) if isinstance(data_dir, int) else data_dir.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(f"not a data directory: {data_dir}")
    return info.st_dev, info.st_ino


def directory_key(identity: DirectoryIdentity) -> str:
    device, inode = identity
    return f"{device:x}-{inode:x}"


def published_socket(identity: DirectoryIdentity, published: str) -> Path | None:
    path = Path(published)
    if (
        path.parent.parent != _SOCKET_ROOT
        or not path.parent.name.startswith(f"plotroom-{os.geteuid()}-")
        or path.name != f"{directory_key(identity)}.sock"
    ):
        return None
    try:
        info = path.parent.lstat()
    except OSError:
        return None
    private = (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700
    )
    return path if private else None


def prepare_local_socket(data_dir: Path | int) -> Path:
    # Keep the path short even for deep data directories or TMPDIR.
    socket_name = f"{directory_key(directory_identity(data_dir))}.sock"
    directory = tempfile.mkdtemp(prefix=f"plotroom-{os.geteuid()}-", dir=_SOCKET_ROOT)
    return Path(directory) / socket_name


def remove_local_socket(path: Path) -> None:
    with contextlib.suppress(OSError):
        if path.is_socket():
            path.unlink()
        path.parent.rmdir()


def write_port(
    data_dir: Path,
    port: int,
    *,
    host: str | None = None,
    local_socket: Path | None = None,
    local_unavailable: bool = False,
    directory_fd: int | None = None,
) -> None:
    """Publish this process's address atomically. Legacy callers may omit the host."""
    if directory_fd is None:
        data_dir.mkdir(parents=True, exist_ok=True)
    target = _publication_path(data_dir, directory_fd)
    scratch = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    fields: dict[str, str | int | None] = {"pid": os.getpid(), "port": port}
    if host is not None:
        fields["host"] = host
    if local_socket is not None:
        fields["local_socket"] = str(local_socket)
    elif local_unavailable:
        fields["local_socket"] = None
    try:
        with open(
            scratch,
            "w",
            encoding="utf-8",
            opener=lambda path, flags: os.open(path, flags | os.O_NOFOLLOW, dir_fd=directory_fd),
        ) as handle:
            json.dump(fields, handle)
        os.replace(scratch, target, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(scratch, dir_fd=directory_fd)


def _publication_path(data_dir: Path, directory_fd: int | None) -> Path:
    return data_dir / PORT_FILE_NAME if directory_fd is None else Path(PORT_FILE_NAME)


def remove_port(data_dir: Path, *, directory_fd: int | None = None) -> None:
    """Delete the port file, if there is one."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(_publication_path(data_dir, directory_fd), dir_fd=directory_fd)


def read_port(data_dir: Path) -> tuple[int, int] | None:
    """The ``(pid, port)`` the file holds, or ``None`` when it is absent or malformed."""
    listener = read_listener(data_dir)
    return None if listener is None else (listener.pid, listener.port)


def read_listener(data_dir: Path, *, directory_fd: int | None = None) -> PublishedListener | None:
    """Read the listener, with no host for legacy records or invalid addresses."""
    try:
        with open(
            _publication_path(data_dir, directory_fd),
            encoding="utf-8",
            opener=lambda path, flags: os.open(path, flags | os.O_NONBLOCK, dir_fd=directory_fd),
        ) as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            decoded: Any = json.load(handle)
    except OSError, ValueError, RecursionError:
        return None
    if not isinstance(decoded, dict):
        return None
    fields = cast("dict[str, Any]", decoded)
    pid, port = fields.get("pid"), fields.get("port")
    if not isinstance(pid, int) or not isinstance(port, int):
        return None
    if isinstance(pid, bool) or isinstance(port, bool):
        return None
    if pid <= 0 or not 0 < port <= _MAX_PORT:
        return None
    host = fields.get("host")
    if not isinstance(host, str):
        return PublishedListener(pid, port, None)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return PublishedListener(pid, port, None)
    local_socket = fields.get("local_socket")
    return PublishedListener(
        pid,
        port,
        host,
        local_socket if isinstance(local_socket, str) else None,
        local_unavailable="local_socket" in fields and local_socket is None,
    )
