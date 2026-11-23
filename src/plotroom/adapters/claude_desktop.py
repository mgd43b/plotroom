"""Read the local Claude Desktop formats observed in the session-state spike."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from plotroom.core.intake import SESSION_MESSAGE, TURN_REPORT
from plotroom.ports.session_reader import SessionEvent

SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _object(value: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _decode(text: str) -> dict[str, Any]:
    try:
        return _object(json.loads(text))
    except ValueError:
        return {}


def _metadata(root: Path, pattern: str) -> Iterator[dict[str, Any]]:
    for path in root.glob(pattern):
        try:
            if path.resolve(strict=True) == path and path.is_file():
                yield _decode(path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            continue


@dataclass
class _Turn:
    index: int
    final_text: str = ""
    recap: str = ""

    def report(self, session: str) -> SessionEvent | None:
        text = self.final_text or self.recap
        return SessionEvent(TURN_REPORT, session, text, turn_index=self.index) if text else None

    def observe(self, message: dict[str, Any]) -> None:
        if not isinstance(message.get("content"), list):
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


def _transcript(path: Path, session: str, still_idle: Callable[[], bool]) -> Iterator[SessionEvent]:
    turn: _Turn | None = None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.endswith("\n"):
                break  # a concurrently appended row may still be incomplete
            row = _decode(line)
            if row.get("type") == "user":
                index = _object(row.get("turnPosition")).get("turnIndex")
                if type(index) is int and index > 0 and (turn is None or index != turn.index):
                    if turn is not None and (report := turn.report(session)) is not None:
                        yield report
                    turn = _Turn(index)
                origin = _object(row.get("origin"))
                if origin.get("kind") == "peer" and isinstance(origin.get("body"), str):
                    key = row.get("uuid")
                    if not isinstance(key, str) or not key:
                        key = f"turn:{index}" if type(index) is int and index > 0 else None
                    if key is not None:
                        yield SessionEvent(
                            SESSION_MESSAGE, session, origin["body"], upstream_id=key
                        )
            elif turn is not None and row.get("type") == "assistant":
                turn.observe(_object(row.get("message")))
    if turn is not None and (report := turn.report(session)) is not None and still_idle():
        yield report


class ClaudeDesktopReader:
    def __init__(self, claude_home: Path, app_support: Path) -> None:
        self._home = claude_home
        self._support = app_support

    async def read(self, session_id: str) -> tuple[SessionEvent, ...]:
        return await asyncio.to_thread(self._read, session_id)

    def _read(self, session: str) -> tuple[SessionEvent, ...]:
        if not SESSION_ID.fullmatch(session):
            return ()
        try:
            states = _metadata(self._support, "claude-code-sessions/*/*/local_*.json")
            if not any(state.get("cliSessionId") == session for state in states):
                return ()
            before = self._status(session)
            if before is None:
                return ()
            paths = [
                path
                for path in self._home.glob(f"projects/*/{session}.jsonl")
                if path.resolve(strict=True) == path and path.is_file()
            ]
            if len(paths) == 1:
                return tuple(
                    _transcript(
                        paths[0],
                        session,
                        lambda: (
                            before[0] == "idle"
                            and before[1] > 0
                            and self._status(session) == before
                        ),
                    )
                )
        except OSError, ValueError:
            pass  # formats and files are local implementation details and can change during reads
        return ()

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
