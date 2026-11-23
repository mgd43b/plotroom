"""The port file: where a daemon publishes the address it actually bound (AD-3).

A client trusts the file only while the data-directory lock is held by the pid the file names
(see ``daemon_start``); the file alone proves nothing, since a killed daemon leaves it behind.
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

PORT_FILE_NAME = "plotroom.port"
_MAX_PORT = 65535


@dataclass(frozen=True)
class PublishedListener:
    pid: int
    port: int
    host: str | None


def write_port(data_dir: Path, port: int, *, host: str | None = None) -> None:
    """Publish this process's address atomically. Legacy callers may omit the host."""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / PORT_FILE_NAME
    scratch = data_dir / f"{PORT_FILE_NAME}.{os.getpid()}.tmp"
    fields: dict[str, str | int] = {"pid": os.getpid(), "port": port}
    if host is not None:
        fields["host"] = host
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
    return PublishedListener(pid, port, host)
