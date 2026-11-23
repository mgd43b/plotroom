"""The port file: where a daemon publishes the address it actually bound (AD-3).

A client trusts the file only while the data-directory lock is held by the pid the file names
(see ``daemon_start``); the file alone proves nothing, since a killed daemon leaves it behind.
"""

from __future__ import annotations

import ipaddress
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

PORT_FILE_NAME = "plotroom.port"
START_DIRECTORY_ENV = "_PLOTROOM_START_DIRECTORY"
_MAX_PORT = 65535

type DirectoryIdentity = tuple[int, int]


@dataclass(frozen=True)
class PublishedListener:
    pid: int
    port: int
    host: str | None
    local_socket: str | None = None


def directory_identity(data_dir: Path) -> DirectoryIdentity:
    info = data_dir.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(f"not a data directory: {data_dir}")
    return info.st_dev, info.st_ino


def local_socket_path(identity: DirectoryIdentity) -> Path:
    # Keep the path within the Unix socket length limit even for deep data directories or TMPDIR.
    return Path("/tmp") / f"plotroom-{os.geteuid()}" / f"{directory_key(identity)}.sock"  # noqa: S108


def directory_key(identity: DirectoryIdentity) -> str:
    device, inode = identity
    return f"{device:x}-{inode:x}"


def private_socket_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700
    )


def prepare_local_socket(data_dir: Path) -> Path:
    path = local_socket_path(directory_identity(data_dir))
    path.parent.mkdir(mode=0o700, exist_ok=True)
    if not private_socket_directory(path.parent):
        raise PermissionError(
            f"local socket directory must be private and owned by this user: {path.parent}"
        )
    return path


def write_port(
    data_dir: Path, port: int, *, host: str | None = None, local_socket: Path | None = None
) -> None:
    """Publish this process's address atomically. Legacy callers may omit the host."""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / PORT_FILE_NAME
    scratch = data_dir / f"{PORT_FILE_NAME}.{os.getpid()}.tmp"
    fields: dict[str, str | int] = {"pid": os.getpid(), "port": port}
    if host is not None:
        fields["host"] = host
    if local_socket is not None:
        fields["local_socket"] = str(local_socket)
    scratch.write_text(json.dumps(fields), encoding="utf-8")
    os.replace(scratch, target)


def remove_port(data_dir: Path) -> None:
    """Delete the port file, if there is one."""
    (data_dir / PORT_FILE_NAME).unlink(missing_ok=True)


def read_port(data_dir: Path) -> tuple[int, int] | None:
    """The ``(pid, port)`` the file holds, or ``None`` when it is absent or malformed."""
    listener = read_listener(data_dir)
    return None if listener is None else (listener.pid, listener.port)


def read_listener(data_dir: Path) -> PublishedListener | None:
    """Read the listener, with no host for legacy records or invalid addresses."""
    try:
        decoded: Any = json.loads((data_dir / PORT_FILE_NAME).read_text(encoding="utf-8"))
    except OSError, ValueError:
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
        pid, port, host, local_socket if isinstance(local_socket, str) else None
    )
