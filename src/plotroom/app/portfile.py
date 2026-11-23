"""The port file: where a daemon publishes the port it actually bound (AD-3).

A config whose port is 0 names no address, so the daemon writes ``{"pid", "port"}`` here once it
listens. A client trusts the file only while the data-directory lock is held by the pid the file
names (see ``daemon_start``); the file alone proves nothing, since a killed daemon leaves it behind.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

PORT_FILE_NAME = "plotroom.port"
_MAX_PORT = 65535


def write_port(data_dir: Path, port: int) -> None:
    """Publish ``port`` as this process's, all at once: a reader sees the old file or the new."""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / PORT_FILE_NAME
    scratch = data_dir / f"{PORT_FILE_NAME}.{os.getpid()}.tmp"
    scratch.write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")
    os.replace(scratch, target)


def remove_port(data_dir: Path) -> None:
    """Delete the port file, if there is one."""
    (data_dir / PORT_FILE_NAME).unlink(missing_ok=True)


def read_port(data_dir: Path) -> tuple[int, int] | None:
    """The ``(pid, port)`` the file holds, or ``None`` when it is absent or malformed."""
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
    return pid, port
