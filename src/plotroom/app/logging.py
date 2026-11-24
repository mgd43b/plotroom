"""Structured JSON logging. Event bodies and credentials never go through it (AD-12)."""

from __future__ import annotations

import contextlib
import json
import logging
import logging.handlers
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from plotroom.app.config import DEFAULT_LOG_BACKUPS, DEFAULT_LOG_MAX_BYTES

# uvicorn adds a colored copy of its message as `color_message`; it is noise in JSON
_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ``ts``, ``level``, ``logger``, ``msg`` and any ``extra`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, separators=(",", ":"))


class BoundedFileHandler(logging.handlers.RotatingFileHandler):
    """A size-rotating log file that also stays bounded when no backups are kept.

    The standard handler never rolls over with zero backups, so the file would grow without
    limit; here the active file is emptied instead.
    """

    def doRollover(self) -> None:
        if self.backupCount > 0:
            super().doRollover()
            return
        if self.stream:
            self.stream.close()
            self.stream = None  # pyright: ignore[reportAttributeAccessIssue]
        with contextlib.suppress(FileNotFoundError):
            os.remove(self.baseFilename)
        if not self.delay:
            self.stream = self._open()


def setup_logging(
    stream: TextIO | None = None,
    level: int = logging.INFO,
    *,
    file: Path | None = None,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backups: int = DEFAULT_LOG_BACKUPS,
) -> None:
    """Route every logger as JSON lines to ``file`` if given, else to ``stream`` (stdout).

    ``file`` is rotated at ``max_bytes``, keeping ``backups`` old files (``.1`` is the newest); its
    directory is created. Raises ``OSError`` when the file cannot be opened, leaving the loggers as
    they were. The stdio proxy passes ``sys.stderr``: its stdout carries the protocol.
    """
    handler: logging.Handler
    if file is not None:
        file.parent.mkdir(parents=True, exist_ok=True)
        handler = BoundedFileHandler(
            file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
    else:
        handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    # httpx logs every request URL at INFO, query string and userinfo included
    for noisy in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
