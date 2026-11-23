"""The data-directory lock (AD-3): one ``plotroom serve`` per data directory."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import IO

LOCK_FILE_NAME = "plotroom.lock"


class LockError(Exception):
    """Another process already owns the data directory."""


class DataDirLock:
    """An exclusive, non-blocking ``flock`` on a lock file inside the data directory.

    The kernel drops the lock when the owning process exits, however it exits.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / LOCK_FILE_NAME
        self._file: IO[str] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            holder = handle.read().strip() or "unknown"
            handle.close()
            raise LockError(
                f"another plotroom serve owns {self._path.parent} (pid {holder}); "
                "stop it or use another data directory"
            ) from None
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._file = handle

    def release(self) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None

    def __enter__(self) -> DataDirLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
