"""Read the local Claude Desktop formats observed in the session-state spike."""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
from collections.abc import Callable, Generator, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO, cast

from plotroom.core.intake import SESSION_MESSAGE, TURN_REPORT
from plotroom.ports.session_reader import SessionEvent

SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _object(value: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _decode(text: bytes) -> dict[str, Any]:
    try:
        return _object(json.loads(text))
    except ValueError:
        return {}


@contextmanager
def _open_regular(root: Path, path: Path) -> Generator[BinaryIO]:
    """Open a discovered file relative to the configured directory without following links."""
    parts = path.relative_to(root).parts
    if not parts or ".." in parts:
        raise ValueError("file must be inside a configured session root")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        directory = os.open(root, directory_flags)
        stack.callback(os.close, directory)
        for part in parts[:-1]:
            directory = os.open(part, directory_flags, dir_fd=directory)
            stack.callback(os.close, directory)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stack.callback(os.close, fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("session data must be a regular file")
        with open(fd, "rb", closefd=False) as stream:
            yield stream


def _metadata(root: Path, pattern: str) -> Iterator[dict[str, Any]]:
    for path in root.glob(pattern):
        try:
            with _open_regular(root, path) as stream:
                entry = _decode(stream.read())
            yield entry
        except OSError, ValueError:
            continue


@dataclass
class _Turn:
    index: int
    final_text: str = ""
    recap: str = ""
    reported: bool = False

    def take_report(self, session: str) -> SessionEvent | None:
        text = self.final_text or self.recap
        if not text or self.reported:
            return None
        self.final_text = self.recap = ""
        try:
            text.encode("utf-8")
        except UnicodeError:
            return None
        self.reported = True
        return SessionEvent(TURN_REPORT, session, text, turn_index=self.index)

    def observe(self, message: dict[str, Any]) -> None:
        if self.reported or not isinstance(message.get("content"), list):
            return
        parts: list[str] = []
        for item in message["content"]:
            block = _object(item)
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            if block.get("type") == "tool_use" and str(block.get("name", "")).endswith("wrap_up"):
                recap = _object(block.get("input")).get("recap")
                if isinstance(recap, str):
                    self.recap = recap
        if parts and message.get("stop_reason") == "end_turn":
            self.final_text = "\n".join(parts)


@dataclass(frozen=True)
class _Position:
    device: int
    inode: int
    offset: int
    turn: _Turn | None


def _transcript(
    stream: BinaryIO, session: str, previous: _Position | None, still_idle: Callable[[], bool]
) -> tuple[tuple[SessionEvent, ...], _Position]:
    info = os.fstat(stream.fileno())
    offset = 0
    turn: _Turn | None = None
    if (
        previous is not None
        and (previous.device, previous.inode) == (info.st_dev, info.st_ino)
        and previous.offset <= info.st_size
    ):
        offset = previous.offset
        turn = replace(previous.turn) if previous.turn is not None else None
    stream.seek(offset)
    events: list[SessionEvent] = []
    for line in stream:
        if not line.endswith(b"\n"):
            break  # a concurrently appended row may still be incomplete
        offset += len(line)
        row = _decode(line)
        if row.get("type") == "user":
            index = _object(row.get("turnPosition")).get("turnIndex")
            if type(index) is int and index > 0 and (turn is None or index != turn.index):
                if turn is not None and (report := turn.take_report(session)) is not None:
                    events.append(report)
                turn = _Turn(index)
            origin = _object(row.get("origin"))
            if origin.get("kind") == "peer" and isinstance(origin.get("body"), str):
                key = row.get("uuid")
                if not isinstance(key, str) or not key:
                    key = f"turn:{index}" if type(index) is int and index > 0 else None
                if key is not None:
                    events.append(
                        SessionEvent(SESSION_MESSAGE, session, origin["body"], upstream_id=key)
                    )
        elif turn is not None and row.get("type") == "assistant":
            turn.observe(_object(row.get("message")))
    if (
        turn is not None
        and not turn.reported
        and still_idle()
        and (report := turn.take_report(session)) is not None
    ):
        events.append(report)
    return tuple(events), _Position(info.st_dev, info.st_ino, offset, turn)


class ClaudeDesktopReader:
    def __init__(self, claude_home: Path, app_support: Path) -> None:
        self._home = claude_home
        self._support = app_support
        self._accepted: dict[str, _Position] = {}
        self._pending: dict[str, _Position] = {}

    async def read(self, session_id: str) -> tuple[SessionEvent, ...]:
        self._pending.pop(session_id, None)
        result = await asyncio.to_thread(self._read, session_id, self._accepted.get(session_id))
        if result is None:
            return ()
        events, position = result
        self._pending[session_id] = position
        return events

    def accept(self, session_id: str) -> None:
        if (position := self._pending.pop(session_id, None)) is not None:
            self._accepted[session_id] = position

    def _read(
        self, session: str, previous: _Position | None
    ) -> tuple[tuple[SessionEvent, ...], _Position] | None:
        if not SESSION_ID.fullmatch(session):
            return None
        try:
            states = _metadata(self._support, "claude-code-sessions/*/*/local_*.json")
            if not any(state.get("cliSessionId") == session for state in states):
                return None
            before = self._status(session)
            if before is None:
                return None
            paths = list(self._home.glob(f"projects/*/{session}.jsonl"))
            if len(paths) == 1:
                with _open_regular(self._home, paths[0]) as stream:
                    return _transcript(
                        stream,
                        session,
                        previous,
                        lambda: (
                            before[0] == "idle"
                            and before[1] > 0
                            and self._status(session) == before
                        ),
                    )
        except OSError, ValueError:
            pass  # formats and files are local implementation details and can change during reads
        return None

    def _status(self, session: str) -> tuple[str, int] | None:
        live = (
            entry
            for entry in _metadata(self._home, "sessions/*.json")
            if entry.get("sessionId") == session
        )
        # A replaced process may leave a registry file briefly. Use the latest status.
        latest = max(live, key=_status_time, default=None)
        if latest is None:
            return None
        status = latest.get("status")
        return (status, _status_time(latest)) if status in ("busy", "idle", "waiting") else None


def _status_time(entry: dict[str, Any]) -> int:
    value = entry.get("statusUpdatedAt")
    return value if type(value) is int else 0
