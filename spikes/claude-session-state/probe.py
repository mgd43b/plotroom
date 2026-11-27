#!/usr/bin/env python3
"""Read-only probe: what does the Claude desktop app expose about its sessions locally?

Spike for issue #31. Standard library only; never writes to the app's files, never
connects to a session's messaging socket, never opens the key files beside the registry.

Three local sources are read (macOS layout; roots are arguments so tests can use a fixture):

  registry    <claude_home>/sessions/*.json
              One file per live process: status (busy | idle | waiting), statusUpdatedAt.
  app state   <app_support>/claude-code-sessions/<account>/<org>/local_*.json
              One file per desktop session: cliSessionId, completedTurns, lastActivityAt and,
              once a turn has ended, lastTurnReport (capped ~400 chars) and lastTurnEdgeAt.
  transcript  <claude_home>/projects/*/<cliSessionId>.jsonl
              Append-only rows. A user row carrying `turnPosition` opens a turn; the last
              assistant `end_turn` row of the turn holds the report; a user row whose
              `origin.kind` is `peer` is an inbound session message.

All of these formats are internal to the app and change between versions. The output is
counts, key names, booleans and opaque session handles unless `events --include-text` is given.

Usage:
    probe.py survey  [--claude-home DIR] [--app-support DIR]
    probe.py events  --session HANDLE [--include-text] [...]  (handle from `survey`)
    probe.py watch   --seconds S [...]                      (status flips and their lag)
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import stat
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

DEFAULT_HOME = os.path.expanduser("~/.claude")
DEFAULT_APP = os.path.expanduser("~/Library/Application Support/Claude")


KNOWN_STATUS = {"busy", "idle", "waiting"}
KNOWN_ORIGIN = {"human", "peer", "host_synthetic", "task_notification"}


def known(value: object, allowed: set[str]) -> str:
    """Only allowlisted strings reach default output; anything else prints as "other"."""
    return value if isinstance(value, str) and value in allowed else "other"


def whole_number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def session_id(value: object) -> str | None:
    if (
        isinstance(value, str)
        and value not in {"", ".", ".."}
        and not any(char in value for char in ("/", "\\", "\0"))
    ):
        return value
    return None


def session_handle(sid: str) -> str:
    return hashlib.sha256(sid.encode("utf-8", errors="surrogatepass")).hexdigest()


def read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError, ValueError, RecursionError:
        return None
    return data if isinstance(data, dict) else None


def _open_transcript(path: str, flags: int) -> int:
    transcript = Path(path)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    projects_fd = os.open(transcript.parent.parent, directory_flags)
    try:
        project_fd = os.open(transcript.parent.name, directory_flags, dir_fd=projects_fd)
        try:
            fd = os.open(transcript.name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=project_fd)
        finally:
            os.close(project_fd)
    finally:
        os.close(projects_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("transcript must be a regular file")
    except OSError:
        os.close(fd)
        raise
    return fd


def read_rows(path: str) -> Iterator[dict[str, Any]]:
    try:
        with open(path, "rb", opener=_open_transcript) as fh:
            for line in fh:
                try:
                    row = json.loads(line.decode("utf-8"))
                except ValueError, RecursionError:
                    continue  # a row being appended may be cut off
                if isinstance(row, dict):
                    yield row
    except OSError:
        pass


def registry(claude_home: str) -> list[dict[str, Any]]:
    """Live processes. Glob *.json only: the neighboring *.key files are never opened."""
    out = []
    pattern = os.path.join(glob.escape(claude_home), "sessions", "*.json")
    for path in sorted(glob.glob(pattern)):
        data = read_json(path)
        if data and session_id(data.get("sessionId")) is not None:
            out.append(data)
    return out


def app_state(app_support: str) -> dict[str, dict[str, Any]]:
    """Desktop session metadata keyed by cliSessionId."""
    pattern = os.path.join(
        glob.escape(app_support), "claude-code-sessions", "*", "*", "local_*.json"
    )
    out = {}
    for path in glob.glob(pattern):
        data = read_json(path)
        if data and session_id(data.get("cliSessionId")) is not None:
            out[data["cliSessionId"]] = data
    return out


def transcript_path(claude_home: str, cli_session_id: str) -> str | None:
    if session_id(cli_session_id) is None:
        return None
    name = f"{cli_session_id}.jsonl"
    try:
        projects = Path(claude_home).resolve(strict=True) / "projects"
        if projects.is_symlink():
            return None
        # Only the project slug is a wildcard. Its encoding is internal to Desktop.
        pattern = os.path.join(glob.escape(str(projects)), "*", glob.escape(name))
        hits = glob.iglob(pattern)
        hit = next(hits, None)
        if hit is None or next(hits, None) is not None:
            return None
        resolved = Path(hit).resolve(strict=True)
        if resolved == Path(hit) and resolved.is_file():
            return str(resolved)
    except OSError, RuntimeError, ValueError:
        pass
    return None


def _assistant_turn_row(turn: dict[str, Any], message: dict[str, Any]) -> None:
    content = message.get("content")
    blocks = content if isinstance(content, list) else []
    if message.get("stop_reason") == "end_turn":
        turn["end_turn_rows"] += 1
        texts = [
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        if texts:
            turn["report"] = "\n".join(texts)
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name, args = block.get("name"), block.get("input")
        if isinstance(name, str) and name.endswith("wrap_up") and isinstance(args, dict):
            recap = args.get("recap")
            if isinstance(recap, str):
                turn["recap"] = recap


def transcript_items(rows: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Yield indexed turn summaries and unindexed peers, retaining one active turn."""
    turn = None
    indexed = False
    for row in rows:
        row_type = row.get("type")
        if row_type == "user":
            position = row.get("turnPosition")
            index = whole_number(position.get("turnIndex")) if isinstance(position, dict) else None
            origin = row.get("origin")
            peer = isinstance(origin, dict) and origin.get("kind") == "peer"
            body = origin.get("body") if peer else None
            if peer and not isinstance(body, str):
                body = ""
            if index is None or index <= 0:
                if peer:
                    if turn is not None:
                        # A peer boundary stops attribution. Require an observed end
                        # before reporting a turn that this boundary interrupted.
                        turn["peer_boundary"] = True
                        yield turn
                        turn = None
                    yield {"turn": None, "body": body}
                if peer or "turnPosition" in row:
                    # A malformed boundary cannot attribute later rows to the prior turn.
                    indexed = False
                continue
            if turn is not None:
                yield turn
            turn = {
                "turn": index,
                "turn_origin": known(row.get("turnOrigin"), KNOWN_ORIGIN),
                "body": body,
                "report": None,
                "recap": None,
                "end_turn_rows": 0,
                "stop_hook_rows": 0,
                "final": False,
            }
            indexed = True
            continue
        if turn is None or not indexed:
            continue
        if row_type == "system" and row.get("subtype") == "stop_hook_summary":
            turn["stop_hook_rows"] += 1
        elif row_type == "assistant" and isinstance(row.get("message"), dict):
            _assistant_turn_row(turn, row["message"])
    if turn is not None:
        turn["final"] = True
        yield turn


def events(
    session: str,
    rows: Iterable[dict[str, Any]],
    still_idle: Callable[[], bool],
    include_text: bool,
) -> Iterator[dict[str, Any]]:
    """Normalized claude.* events. A peer can have an unknown turn index.

    A later indexed turn or a fresh idle observation closes an indexed turn, even if
    its `end_turn` row has not been written. An unindexed peer ends attribution without
    proving that the previous turn completed, so only an observed `end_turn` can produce
    a report at that boundary."""
    for turn in transcript_items(rows):
        if turn.get("final") and not still_idle():
            continue  # still running (or the process is gone and we cannot tell)
        if turn["body"] is not None:
            event: dict[str, Any] = {
                "kind": "claude.session_message",
                "session": session,
                "turn": turn["turn"],
                "body_len": len(turn["body"]),
            }
            if include_text:
                event["body"] = turn["body"]
            yield event
        if turn["turn"] is None or (turn.get("peer_boundary") and not turn["end_turn_rows"]):
            continue
        report = turn["recap"] or turn["report"]
        event = {
            "kind": "claude.turn_report",
            "session": session,
            "turn": turn["turn"],
            "origin": turn["turn_origin"],
            "has_end_turn": turn["end_turn_rows"] > 0,
            "has_recap": turn["recap"] is not None,
            "report_len": len(report or ""),
        }
        if include_text:
            event["report"] = report
        yield event


def app_state_event(
    session: str, state: dict[str, Any], include_text: bool
) -> dict[str, Any] | None:
    """The app's own one-slot record of the latest turn: a report capped at ~400 characters
    and the time the turn ended. It is overwritten by the next turn, so a reader that polls
    slower than turns end loses turns; the transcript keeps them all."""
    report = state.get("lastTurnReport")
    edge = whole_number(state.get("lastTurnEdgeAt"))
    if not isinstance(report, str) or edge is None:
        return None
    event: dict[str, Any] = {
        "kind": "claude.turn_report",
        "source": "app_state",
        "session": session,
        "turn_edge_at_ms": edge,
        "completed_turns": whole_number(state.get("completedTurns")),
        "report_len": len(report),
    }
    if include_text:
        event["report"] = report
    return event


def transcript_counts(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    groups = reports = messages = 0
    stop_hooks: Counter[int] = Counter()
    end_turns: Counter[int] = Counter()
    for turn in transcript_items(rows):
        if turn["body"] is not None:
            messages += 1
        if turn["turn"] is None:
            continue
        groups += 1
        reports += bool(turn["report"] or turn["recap"])
        stop_hooks[turn["stop_hook_rows"]] += 1
        end_turns[turn["end_turn_rows"]] += 1
    return {
        "turn_groups": groups,
        "reports_found": reports,
        "inbound_peer_messages": messages,
        "stop_hooks_per_turn": dict(stop_hooks),
        "end_turn_rows_per_turn": dict(end_turns),
    }


def survey(claude_home: str, app_support: str) -> dict[str, Any]:
    live = registry(claude_home)
    meta = app_state(app_support)
    per_session = []
    for ordinal, entry in enumerate(live, 1):
        sid = entry["sessionId"]
        path = transcript_path(claude_home, sid)
        state = meta.get(sid) or {}
        per_session.append(
            {
                "session_n": ordinal,
                "session_handle": session_handle(sid),
                "status": known(entry.get("status"), KNOWN_STATUS),
                "has_app_state": sid in meta,
                "has_transcript": path is not None,
                "completed_turns_field": whole_number(state.get("completedTurns")),
                "app_state_report_len": len(state["lastTurnReport"])
                if isinstance(state.get("lastTurnReport"), str)
                else None,
                "app_state_has_turn_edge": whole_number(state.get("lastTurnEdgeAt")) is not None,
                "app_state_has_last_assistant_uuid": isinstance(
                    state.get("lastAssistantUuid"), str
                ),
                **transcript_counts(read_rows(path) if path else ()),
            }
        )
    return {
        "registry_files": len(live),
        "registry_keys": sorted({k for e in live for k in e}),
        "registry_statuses": dict(Counter(known(e.get("status"), KNOWN_STATUS) for e in live)),
        "app_state_files": len(meta),
        "app_state_keys": sorted({k for m in meta.values() for k in m}),
        "has_messaging_socket_field": any("messagingSocketPath" in e for e in live),
        "sessions": per_session,
    }


def watch(claude_home: str, seconds: float, interval: float = 0.5) -> dict[str, Any]:
    """Poll the registry. For each busy -> idle flip report how stale the registry's own
    timestamp was when the flip was seen and whether the transcript file had grown since
    the previous poll. It does not check that the turn's last row had landed."""
    ordinals: dict[str, int] = {}
    flips: list[dict[str, Any]] = []
    previous: dict[str, tuple[str, os.stat_result | None]] = {}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = {}
        entries = registry(claude_home)
        session_counts = Counter(entry["sessionId"] for entry in entries)
        for entry in entries:
            sid = entry["sessionId"]
            if session_counts[sid] != 1:
                continue
            n = ordinals.setdefault(sid, len(ordinals) + 1)
            status = known(entry.get("status"), KNOWN_STATUS)
            path = transcript_path(claude_home, sid)
            try:
                sample = os.stat(path, follow_symlinks=False) if path else None
            except OSError:
                sample = None
            if sample is not None and not stat.S_ISREG(sample.st_mode):
                sample = None
            last_status, last_sample = previous.get(sid, ("other", None))
            if last_status == "busy" and status == "idle":
                seen_at = time.time()
                seen_at_ms = int(seen_at * 1000)
                updated = whole_number(entry.get("statusUpdatedAt"))
                flips.append(
                    {
                        "session_n": n,
                        "flip_seen_at": seen_at,
                        "transcript_grew_since_last_poll": (
                            sample is not None
                            and last_sample is not None
                            and (sample.st_dev, sample.st_ino)
                            == (last_sample.st_dev, last_sample.st_ino)
                            and sample.st_size > last_sample.st_size
                        ),
                        "registry_status_age_ms": seen_at_ms - updated
                        if updated is not None and 0 < updated <= seen_at_ms
                        else None,
                    }
                )
            current[sid] = status, sample
        previous = current
        time.sleep(interval)
    return {"polled_seconds": seconds, "sessions_seen": len(ordinals), "busy_to_idle_flips": flips}


def registry_entry(claude_home: str, handle: str | None) -> dict[str, Any] | None:
    matches = [
        entry for entry in registry(claude_home) if session_handle(entry["sessionId"]) == handle
    ]
    return matches[0] if len(matches) == 1 else None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["survey", "events", "watch"])
    parser.add_argument("--claude-home", default=DEFAULT_HOME)
    parser.add_argument("--app-support", default=DEFAULT_APP)
    parser.add_argument("--session", help="session_handle from `survey` (events)")
    parser.add_argument(
        "--include-text", action="store_true", help="events: include report and message text"
    )
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    if args.command == "survey":
        print(json.dumps(survey(args.claude_home, args.app_support), indent=2, sort_keys=True))
    elif args.command == "watch":
        print(json.dumps(watch(args.claude_home, args.seconds), indent=2, sort_keys=True))
    else:
        entry = registry_entry(args.claude_home, args.session)
        if entry is None:
            print("events needs the handle of one live session from survey", file=sys.stderr)
            return 2
        sid = entry["sessionId"]
        path = transcript_path(args.claude_home, sid)
        label = session_handle(sid)

        def still_idle() -> bool:
            return entry.get("status") == "idle" and entry == registry_entry(
                args.claude_home, label
            )

        for event in events(label, read_rows(path) if path else (), still_idle, args.include_text):
            print(json.dumps(event, sort_keys=True))
        state_event = app_state_event(
            label, app_state(args.app_support).get(sid) or {}, args.include_text
        )
        if state_event:
            print(json.dumps(state_event, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
