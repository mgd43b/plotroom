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
counts, key names and booleans only unless `events --include-text` is given.

Usage:
    probe.py survey  [--claude-home DIR] [--app-support DIR]
    probe.py events  --session N [--include-text] [...]     (N: ordinal from `survey`)
    probe.py watch   --seconds S [...]                      (status flips and their lag)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Iterator
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


def read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_rows(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # a row being appended may be cut off
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def registry(claude_home: str) -> list[dict[str, Any]]:
    """Live processes. Glob *.json only: the neighboring *.key files are never opened."""
    out = []
    for path in sorted(glob.glob(os.path.join(claude_home, "sessions", "*.json"))):
        data = read_json(path)
        if data and data.get("sessionId"):
            out.append(data)
    return out


def app_state(app_support: str) -> dict[str, dict[str, Any]]:
    """Desktop session metadata keyed by cliSessionId."""
    pattern = os.path.join(app_support, "claude-code-sessions", "*", "*", "local_*.json")
    out = {}
    for path in glob.glob(pattern):
        data = read_json(path)
        if data and data.get("cliSessionId"):
            out[data["cliSessionId"]] = data
    return out


def transcript_path(claude_home: str, cli_session_id: str) -> str | None:
    # Glob rather than rebuild the project slug: the slug rules are internal.
    hits = glob.glob(os.path.join(claude_home, "projects", "*", f"{cli_session_id}.jsonl"))
    return hits[0] if hits else None


def _blocks(row: dict[str, Any]) -> list[dict[str, Any]]:
    content = (row.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def turns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group transcript rows into turns keyed by turnPosition.turnIndex."""
    out: list[dict[str, Any]] = []
    for row in rows:
        position = row.get("turnPosition")
        if row.get("type") == "user" and isinstance(position, dict) and "turnIndex" in position:
            origin = row.get("origin")
            out.append(
                {
                    "turn": position["turnIndex"],
                    "turn_origin": known(row.get("turnOrigin"), KNOWN_ORIGIN),
                    "inbound": origin
                    if isinstance(origin, dict) and origin.get("kind") == "peer"
                    else None,
                    "report": None,
                    "recap": None,
                    "end_turn_rows": 0,
                    "stop_hook_rows": 0,
                    "sent": [],
                }
            )
            continue
        if not out:
            continue
        cur = out[-1]
        if row.get("type") == "system" and row.get("subtype") == "stop_hook_summary":
            cur["stop_hook_rows"] += 1
        elif row.get("type") == "assistant":
            message = row.get("message") or {}
            texts = [b.get("text", "") for b in _blocks(row) if b.get("type") == "text"]
            if message.get("stop_reason") == "end_turn":
                cur["end_turn_rows"] += 1
                if texts:
                    cur["report"] = "\n".join(texts)
            for block in _blocks(row):
                if block.get("type") != "tool_use":
                    continue
                name, args = str(block.get("name", "")), block.get("input") or {}
                if name.endswith("wrap_up") and isinstance(args.get("recap"), str):
                    cur["recap"] = args["recap"]
                if name.endswith("SendMessage") and "to" in args:
                    cur["sent"].append(args)
    return out


def events(
    session: str, rows: list[dict[str, Any]], status: str | None, include_text: bool
) -> Iterator[dict[str, Any]]:
    """Normalized claude.* events. The key (session, turn) is what a reader can derive
    on its own; a turn is closed once a later turn exists or the process is idle. A closed
    turn with no `end_turn` row (interrupted, or its last row not flushed yet) is still
    emitted, with has_end_turn false. A process that left the registry is not read, so its
    last turn is only seen if a later poll found it idle first."""
    all_turns = turns(rows)
    for position, turn in enumerate(all_turns):
        last = position == len(all_turns) - 1
        if last and status != "idle":
            continue  # still running (or the process is gone and we cannot tell)
        if turn["inbound"] is not None:
            event: dict[str, Any] = {
                "kind": "claude.session_message",
                "session": session,
                "turn": turn["turn"],
                "body_len": len(str(turn["inbound"].get("body", ""))),
            }
            if include_text:
                event["body"] = turn["inbound"].get("body")
            yield event
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
    if not isinstance(report, str) or not isinstance(state.get("lastTurnEdgeAt"), int):
        return None
    event: dict[str, Any] = {
        "kind": "claude.turn_report",
        "source": "app_state",
        "session": session,
        "turn_edge_at_ms": state["lastTurnEdgeAt"],
        "completed_turns": whole_number(state.get("completedTurns")),
        "report_len": len(report),
    }
    if include_text:
        event["report"] = report
    return event


def survey(claude_home: str, app_support: str) -> dict[str, Any]:
    live = registry(claude_home)
    meta = app_state(app_support)
    per_session = []
    for ordinal, entry in enumerate(live, 1):
        sid = entry["sessionId"]
        path = transcript_path(claude_home, sid)
        grouped = turns(read_rows(path)) if path else []
        state = meta.get(sid) or {}
        per_session.append(
            {
                "session_n": ordinal,
                "status": known(entry.get("status"), KNOWN_STATUS),
                "has_app_state": sid in meta,
                "has_transcript": path is not None,
                "completed_turns_field": whole_number(state.get("completedTurns")),
                "app_state_report_len": len(state["lastTurnReport"])
                if isinstance(state.get("lastTurnReport"), str)
                else None,
                "app_state_has_turn_edge": isinstance(state.get("lastTurnEdgeAt"), int),
                "app_state_has_last_assistant_uuid": isinstance(
                    state.get("lastAssistantUuid"), str
                ),
                "turn_groups": len(grouped),
                "reports_found": sum(1 for t in grouped if t["report"] or t["recap"]),
                "inbound_peer_messages": sum(1 for t in grouped if t["inbound"]),
                "stop_hooks_per_turn": dict(Counter(t["stop_hook_rows"] for t in grouped)),
                "end_turn_rows_per_turn": dict(Counter(t["end_turn_rows"] for t in grouped)),
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
    last_status: dict[str, str | None] = {}
    flips: list[dict[str, Any]] = []
    size_seen: dict[str, int] = {}
    deadline = time.time() + seconds
    while time.time() < deadline:
        for entry in registry(claude_home):
            sid = entry["sessionId"]
            n = ordinals.setdefault(sid, len(ordinals) + 1)
            status = entry.get("status")
            path = transcript_path(claude_home, sid)
            try:
                size = os.path.getsize(path) if path else -1
            except OSError:
                size = -1
            if last_status.get(sid) == "busy" and status == "idle":
                flips.append(
                    {
                        "session_n": n,
                        "flip_seen_at": time.time(),
                        "transcript_grew_since_last_poll": size != size_seen.get(sid),
                        "registry_status_age_ms": int(time.time() * 1000)
                        - (whole_number(entry.get("statusUpdatedAt")) or 0),
                    }
                )
            last_status[sid], size_seen[sid] = status, size
        time.sleep(interval)
    return {"polled_seconds": seconds, "sessions_seen": len(ordinals), "busy_to_idle_flips": flips}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["survey", "events", "watch"])
    parser.add_argument("--claude-home", default=DEFAULT_HOME)
    parser.add_argument("--app-support", default=DEFAULT_APP)
    parser.add_argument("--session", type=int, help="1-based ordinal from `survey` (events)")
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
        live = registry(args.claude_home)
        if not args.session or not 1 <= args.session <= len(live):
            print(f"events needs --session N, 1..{len(live)}", file=sys.stderr)
            return 2
        entry = live[args.session - 1]
        sid = entry["sessionId"]
        path = transcript_path(args.claude_home, sid)
        label = f"session-{args.session}"  # never print the real id
        for event in events(
            label, read_rows(path) if path else [], entry.get("status"), args.include_text
        ):
            print(json.dumps(event, sort_keys=True))
        state_event = app_state_event(
            label, app_state(args.app_support).get(sid) or {}, args.include_text
        )
        if state_event:
            print(json.dumps(state_event, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
