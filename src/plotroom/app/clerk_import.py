"""Read the clerk's Markdown templates as data for the running local daemon."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types import TextContent

if TYPE_CHECKING:
    from plotroom.core.clerk_import import PullImportRow, SkipImportRow, TriggerImportRow

log = logging.getLogger("plotroom")

_LINE_END = re.compile(r"\r\n?|\n")
_HEADING = re.compile(r" {0,3}##[ \t]+(.+?)[ \t]*")
_COMMENT_START = re.compile(r" {0,3}<!--")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_LABEL = re.compile(r"(T[0-9]+):[ \t]*(.*)")
_PR_CONDITION = re.compile(
    r"#([0-9]+) merges(?: \((?:tell a waiting lane|the coordinator decides what follows|"
    r"a lane whose post-merge list is already empty)\))?"
)
_BRANCH_CONDITION = re.compile(r"branch `([^`<>]+)` opens a PR that merges")
_FIRED = re.compile(r"FIRED ([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9:.+Z-]+) ([0-9a-fA-F]{7,40})")
_MESSAGE = re.compile(r'- Message `([^`]+)`:[ \t]*"(.+)"')
_NOTIFY = re.compile(r'- Tell the coordinator:[ \t]*"(.+?)"( Don\'t message the lane\.)?')
_ARCHIVE_OWNER = re.compile(r'- Owner: [^`]+ `[^`]+`\. Its post-merge list is "none"\.')
_TRIGGER_TITLE = "# Clerk triggers"
_LEDGER_TITLE = re.compile(r"# Clerk ledger: ([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
_OPEN_HEADING = re.compile(r"open prs(?:[ \t]+#+)?", re.IGNORECASE)
_OPEN_HEADER = ("pr", "owner (lane)", "kind", "channel", "state", "after merge")
_SKIP_HEADER = ("pr", "why, and who asked")


def _sections(text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    fence = ""
    comment = False
    for line in _LINE_END.split(text):
        if not fence and (comment or _COMMENT_START.match(line)):
            # Never treat heading-like lines inside Markdown comments as live records.
            rest = line
            while True:
                if comment:
                    _, close, rest = rest.partition("-->")
                    if not close:
                        break
                    comment = False
                _, start, rest = rest.partition("<!--")
                if not start:
                    break
                comment = True
            continue
        marker = _FENCE.fullmatch(line)
        if marker is not None:
            delimiter, rest = marker.groups()
            if not fence:
                if delimiter.startswith("~") or "`" not in rest:
                    fence = delimiter
            elif delimiter.startswith(fence) and not rest.strip(" \t"):
                fence = ""
        heading = _HEADING.fullmatch(line) if not (fence or marker) else None
        if heading is not None:
            sections.append((heading[1], []))
        elif sections:
            sections[-1][1].append(line)
    return sections


def _document_title(text: str) -> str:
    line = next((line for line in _LINE_END.split(text) if line.strip(" \t")), "")
    title = line.lstrip(" ")
    return title.rstrip(" \t") if len(line) - len(title) <= 3 else ""


def _number(text: str) -> int | None:
    if len(text) > 19 or not text.isascii() or not text.isdecimal():
        return None
    number = int(text)
    return number if 0 < number <= 2**63 - 1 else None


def _predicate(condition: str) -> dict[str, Any] | None:
    if (match := _PR_CONDITION.fullmatch(condition)) and (pr := _number(match[1])):
        return {"pr_merged": {"number": pr}}
    if (match := _BRANCH_CONDITION.fullmatch(condition)) and match[1].strip():
        return {"pr_merged": {"branch_glob": match[1]}}
    return None


def _fired_marker(line: str) -> bool:
    if line.startswith("- "):
        line = line[2:]
    for wrapper in ("`", "**"):
        if line.startswith(wrapper) and line.endswith(wrapper):
            line = line[len(wrapper) : -len(wrapper)]
            break
    match = _FIRED.fullmatch(line)
    if match is None:
        return False
    try:
        datetime.fromisoformat(match[1])
    except ValueError:
        return False
    return True


def _exact_trigger(row: TriggerImportRow, condition: str, body: list[str]) -> None:
    predicate = _predicate(condition)
    if predicate is None:
        return
    fired = False
    channel = None
    message = None
    notify = False
    owner = False
    for original in body:
        line = original.rstrip()
        if not line:
            continue
        if not fired and _fired_marker(line):
            fired = True
        elif message is None and (match := _MESSAGE.fullmatch(line)):
            channel, message = match.groups()
        elif message is None and not notify and (match := _NOTIFY.fullmatch(line)):
            if bool(match[2]) != owner:
                return
            message = match[1]
            notify = True
        elif not notify and channel is not None and line == "- Tell the coordinator.":
            notify = True
        elif not owner and message is None and _ARCHIVE_OWNER.fullmatch(line):
            owner = True
        else:
            return
    if not message or not message.strip() or (owner and channel is not None):
        return
    row.update(predicate=predicate, text=message, channel=channel, notify=notify, fired=fired)


def parse_triggers(text: str) -> list[TriggerImportRow]:
    """Recognize complete template entries, retaining unsupported sections for binding."""
    rows: dict[str, TriggerImportRow] = {}
    for heading, body in _sections(text):
        source = ("## " + heading + "\n" + "\n".join(body)).rstrip()
        match = _LABEL.fullmatch(heading)
        label = match[1] if match else "unrecognized-" + hashlib.sha256(source.encode()).hexdigest()
        row: TriggerImportRow = {
            "label": label,
            "source": source,
            "predicate": None,
            "text": None,
            "channel": None,
            "notify": False,
            "fired": False,
        }
        if match is not None:
            _exact_trigger(row, match[2], body)
        if label in rows and rows[label] != row:
            raise ValueError(f"triggers template contains conflicting entries for {label}")
        rows[label] = row
    if not rows and _document_title(text) != _TRIGGER_TITLE:
        raise ValueError("expected a clerk triggers template")
    return list(rows.values())


def _cells(line: str) -> tuple[str, ...]:
    if not re.match(r" {0,3}\|", line):
        return ()
    line = line.strip()
    if not line.endswith("|"):
        return ()
    # Keep escaped pipes in prose and skip the leading/trailing table delimiters.
    protected = re.split(r"(\\\\|\\\|)", line[1:-1])
    parts: list[str] = [""]
    for index, segment in enumerate(protected):
        if index % 2:
            parts[-1] += segment[1:]
            continue
        head, *rest = segment.split("|")
        parts[-1] += head
        parts.extend(rest)
    return tuple(value.strip() for value in parts)


def _table(body: list[str], header: tuple[str, ...]) -> list[tuple[str, ...]]:
    start = next((index for index, line in enumerate(body) if line.strip()), None)
    if start is None:
        return []
    names = _cells(body[start])
    separator = _cells(body[start + 1]) if start + 1 < len(body) else ()
    if (
        tuple(name.lower() for name in names) != header
        or len(separator) != len(header)
        or any(not re.fullmatch(r":?-{3,}:?", value) for value in separator)
    ):
        raise ValueError(f"unexpected Markdown table columns or separator for {', '.join(header)}")
    rows: list[tuple[str, ...]] = []
    for line in body[start + 2 :]:
        if not line.strip():
            continue
        cells = _cells(line)
        if len(cells) != len(header):
            raise ValueError(f"unexpected content or row width in table for {', '.join(header)}")
        rows.append(cells)
    return rows


def _unquote(text: str) -> str:
    if len(text) > 1 and text[0] == text[-1] == "`":
        return text[1:-1]
    return text


def _pull_number(cell: str) -> int:
    value = _unquote(cell)
    if not value.startswith("#") or (number := _number(value[1:])) is None:
        raise ValueError("expected a positive #PR number in the clerk ledger")
    return number


def parse_ledger(text: str) -> tuple[str, list[PullImportRow], list[SkipImportRow]]:
    """Read the two supported tables. Flake receipts and historical audit stay in the source."""
    match = _LEDGER_TITLE.fullmatch(_document_title(text))
    if match is None:
        raise ValueError("expected a clerk ledger with an owner/repository title")
    pulls: dict[int, PullImportRow] = {}
    skips: dict[int, SkipImportRow] = {}
    sections = _sections(text)
    if not any(
        heading.lower() == "open prs"
        or heading.lower() == "skip list"
        or heading.lower().startswith("skip list (")
        for heading, _ in sections
    ):
        raise ValueError("expected the clerk ledger template")
    # A heading with closing hashes contributes to ambiguity without changing what gets imported.
    open_sections = [body for heading, body in sections if _OPEN_HEADING.fullmatch(heading)]
    if len(open_sections) > 1 and any(
        (cells := _cells(line)) and cells[0].casefold() == "none"
        for body in open_sections
        for line in body
    ):
        raise ValueError("the empty Open PRs marker requires a single Open PRs table")
    for heading, body in sections:
        name = heading.lower()
        if name == "open prs":
            rows = _table(body, _OPEN_HEADER)
            if rows and rows[0][0].casefold() == "none":
                if len(rows) != 1 or any(rows[0][1:]):
                    raise ValueError(
                        "the empty Open PRs marker requires one table with no other data"
                    )
                continue
            for pr, owner, _, channel, state, after_merge in rows:
                if pr.lower() == "(soon)":
                    continue
                number = _pull_number(pr)
                row: PullImportRow = {
                    "pr": number,
                    "worker": _unquote(owner),
                    "channel": _unquote(channel),
                    "state": _unquote(state),
                    "after_merge": after_merge,
                }
                if number in pulls and pulls[number] != row:
                    raise ValueError(f"ledger has conflicting open rows for #{number}")
                pulls[number] = row
        elif name == "skip list" or name.startswith("skip list ("):
            for pr, reason in _table(body, _SKIP_HEADER):
                number = _pull_number(pr)
                row_skip: SkipImportRow = {"pr": number, "reason": reason}
                if number in skips and skips[number] != row_skip:
                    raise ValueError(f"ledger has conflicting skip rows for #{number}")
                skips[number] = row_skip
    return match[1].lower(), list(pulls.values()), list(skips.values())


async def _submit(config: Path | None, token_id: str | None, args: dict[str, Any]) -> int:
    command = ["-m", "plotroom", "connect", "--stdio", "--existing-local"]
    for flag, value in (("--config", config), ("--token-id", token_id)):
        if value is not None:
            command.extend((flag, str(value)))
    env = os.environ.copy()
    env.pop("PLOTROOM_TOKEN", None)
    env.pop("PLOTROOM_URL", None)
    proxy = StdioServerParameters(command=sys.executable, args=command, env=env)
    async with Client(proxy, mode="legacy") as client:
        response = await client.call_tool("clerk_import", args)
        first = next(iter(response.content), None)
        if response.is_error or not isinstance(first, TextContent):
            log.error("local import server did not accept the request")
            return 1
        result = json.loads(first.text)
        if not result["ok"]:
            log.error("local import refused", extra={"reason": result["error"]["message"]})
            return 1
        print(json.dumps(result["data"]))
        return 0


def run_import(
    config: Path | None, token_id: str | None, triggers: Path | None, ledger: Path | None
) -> int:
    if triggers is None and ledger is None:
        log.error("import requires --triggers, --ledger, or both")
        return 2
    try:
        trigger_rows: list[TriggerImportRow] = []
        if triggers is not None:
            source = triggers.read_text(encoding="utf-8-sig")
            if _document_title(source) != _TRIGGER_TITLE:
                raise ValueError("expected a clerk triggers file with its template title")
            trigger_rows = parse_triggers(source)
        repository, pulls, skips = (
            parse_ledger(ledger.read_text(encoding="utf-8-sig")) if ledger else (None, [], [])
        )
    except (OSError, UnicodeError, ValueError) as exc:
        log.error("cannot read clerk templates", extra={"reason": str(exc)})
        return 2
    try:
        return asyncio.run(
            _submit(
                config,
                token_id,
                {
                    "triggers": trigger_rows,
                    "pulls": pulls,
                    "skips": skips,
                    "ledger_repository": repository,
                },
            )
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log.error("local import connection failed", extra={"reason": type(exc).__name__})
        return 1
