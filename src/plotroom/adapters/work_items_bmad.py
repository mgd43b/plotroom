"""Read the ready-to-start view of a local BMad repo ticket tree."""

import asyncio
import contextlib
import json
import os
import re
import stat
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, BinaryIO, cast

from plotroom.ports.work_items import MAX_WORK_ITEM_BODY_BYTES, WorkItem

_READER_TIMEOUT = 15.0
_FIND_OUTPUT_LIMIT = 2 * MAX_WORK_ITEM_BODY_BYTES
_UNKNOWN_REFERENCE = "no ticket matches "
_TICKET_ID = re.compile(r"^[0-9A-Za-z]+$")


class TicketTreeError(ValueError):
    """The local source could not read the configured ticket tree."""


def _read_source_file(scope: Path, path: Path) -> str:
    try:
        parts = path.relative_to(scope).parts
        if not parts or ".." in parts:
            raise ValueError("not a ticket file")
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        with contextlib.ExitStack() as stack:
            directory = os.open(scope.anchor, directory_flags)
            stack.callback(os.close, directory)
            for part in (*scope.parts[1:], *parts[:-1]):
                directory = os.open(part, directory_flags, dir_fd=directory)
                stack.callback(os.close, directory)
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with open(fd, "rb") as file:
                if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                    raise ValueError("not a regular file")
                content = file.read(MAX_WORK_ITEM_BODY_BYTES + 1)
        if len(content) > MAX_WORK_ITEM_BODY_BYTES:
            raise TicketTreeError("the ticket file exceeds the size limit")
        return content.decode("utf-8")
    except TicketTreeError:
        raise
    except (OSError, ValueError) as exc:
        raise TicketTreeError(
            "cannot read a regular UTF-8 ticket file in the configured tree"
        ) from exc


def _ticket_id(value: object) -> int | str | None:
    # Match the trusted reader's numeric and lettered identifiers.
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _TICKET_ID.match(value) and value not in ("true", "false"):
        return int(value) if value.isdigit() else value
    return None


def _compact_body(document: str, ticket_id: int | str) -> str | None:
    try:
        # The BMad reader accepts a UTF-8 BOM and reads through universal newlines.
        text = document.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        entries = tomllib.loads(text).get("entry", [])
        if not isinstance(entries, list):
            raise ValueError("entry must be a list")
        matches: list[dict[str, Any]] = []
        for row in cast("list[Any]", entries):
            if not isinstance(row, dict):
                raise ValueError("entry must contain tables")
            candidate = cast("dict[str, Any]", row)
            if _ticket_id(candidate.get("id")) == ticket_id:
                matches.append(candidate)
    except ValueError as exc:
        raise TicketTreeError("the compact ticket breakdown is invalid") from exc
    if not matches:
        return None
    if len(matches) != 1:
        raise TicketTreeError("the compact ticket breakdown has duplicate ids")
    ticket = matches[0]

    def string(field: str) -> str:
        return str(ticket.get(field, ""))

    def lines(field: str) -> list[str]:
        values = ticket.get(field, [])
        if not isinstance(values, list):
            raise TicketTreeError(f"invalid compact ticket {field}")
        return [str(value) for value in cast("list[Any]", values)]

    body = f"# {string('title')}\n\n## Description\n\n{string('description')}\n\n"
    body += f"## Requirements\n\nVerify: {string('verify')}\n"
    if references := lines("references"):
        body += "\n## References\n\n" + "".join(f"- {reference}\n" for reference in references)
    notes = lines("notes")
    if unknown := string("unknown"):
        notes = [f"Unknown: {unknown}", *notes]
    if notes:
        body += "\n## Notes\n\n" + "".join(f"- {note}\n" for note in notes)
    if len(body.encode("utf-8")) > MAX_WORK_ITEM_BODY_BYTES:
        raise TicketTreeError("the compact ticket exceeds the size limit")
    return body


class BmadTicketTree:
    """Use the installed BMad reader so dependencies and plan state follow its rules.

    The runner comes from a trusted BMad installation selected by the daemon operator.
    Ticket files themselves are inputs to the read-only next command.
    """

    def __init__(self, tree: Path, installation: Path, *, project_root: Path | None = None) -> None:
        self.tree = tree.resolve()
        self.installation = installation
        self.reader = installation / "_bmad" / "method" / "scripts" / "tickets.py"
        self.project_root = project_root

    async def ready_items(self) -> tuple[WorkItem, ...]:
        reader, root, tree = self._reader_paths()
        with tempfile.TemporaryFile() as stdout:
            code = await self._read(reader, root, stdout, ("next", str(tree)))
            if code == 2:
                raise TicketTreeError("the ticket tree must use BMad's repo store")
            if code != 0:
                raise TicketTreeError("the BMad reader could not read the ticket tree")
            stdout.seek(0)
            return self._parse(stdout)

    async def body(self, ref: str) -> str | None:
        reader, root, tree = self._reader_paths()
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            code = await self._read(reader, root, stdout, ("find", "--", str(tree), ref), stderr)
            if code == 2:
                raise TicketTreeError("the ticket tree must use BMad's repo store")
            if code != 0:
                stderr.seek(0)
                try:
                    problem = json.loads(stderr.read(_FIND_OUTPUT_LIMIT)).get("error")
                except AttributeError, ValueError:
                    problem = None
                if isinstance(problem, str) and problem.startswith(_UNKNOWN_REFERENCE):
                    return None
                raise TicketTreeError("the BMad reader could not find a unique ticket")
            stdout.seek(0)
            try:
                packed = stdout.read(_FIND_OUTPUT_LIMIT + 1)
                if len(packed) > _FIND_OUTPUT_LIMIT:
                    raise ValueError("find result exceeds the size limit")
                ticket: dict[str, Any] = json.loads(packed)
                value = ticket["story_file"]
                if value is not None and not isinstance(value, str):
                    raise ValueError("invalid story file")
            except (KeyError, TypeError, ValueError) as exc:
                raise TicketTreeError("the BMad reader returned no ticket body reference") from exc
        scope = tree.parent if tree.name == "tickets" else tree
        if value is None:
            plan = ticket.get("plan")
            ticket_id = _ticket_id(ticket.get("id"))
            if not isinstance(plan, str) or not plan or ticket_id is None:
                raise TicketTreeError("the BMad reader returned no compact ticket location")
            path = Path(plan).parent / "tickets.toml"
            if not path.is_relative_to(scope):
                return None
            document = await asyncio.to_thread(_read_source_file, scope, path)
            return _compact_body(document, ticket_id)
        path = Path(value)
        if not path.is_relative_to(scope):
            return None
        if path.suffix != ".md":
            raise TicketTreeError("the BMad reader returned an invalid story file")
        return await asyncio.to_thread(_read_source_file, scope, path)

    def _reader_paths(self) -> tuple[Path, Path, Path]:
        if not self.tree.is_dir():
            raise TicketTreeError("configured ticket_tree directory does not exist")
        tree = self.tree
        root = self.project_root or next(
            (p for p in (tree, *tree.parents) if (p / "_bmad").is_dir()), tree
        )
        root = root.resolve()
        reader = self.reader.resolve()
        if reader.is_relative_to(root) or not reader.is_file():
            raise TicketTreeError("configure a trusted BMad reader outside the mirrored checkout")
        return reader, root, tree

    async def _read(
        self,
        reader: Path,
        root: Path,
        stdout: BinaryIO,
        args: tuple[str, ...],
        stderr: BinaryIO | None = None,
    ) -> int:
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-B",
                str(reader),
                "--project-root",
                str(root),
                *args,
                stdout=stdout,
                stderr=asyncio.subprocess.DEVNULL if stderr is None else stderr,
                cwd=self.installation,
            )
        except OSError as exc:
            raise TicketTreeError("cannot start the installed BMad reader") from exc
        try:
            async with asyncio.timeout(_READER_TIMEOUT):
                await process.wait()
        except TimeoutError as exc:
            raise TicketTreeError("the BMad reader timed out") from exc
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        assert process.returncode is not None
        return process.returncode

    def _parse(self, stdout: BinaryIO) -> tuple[WorkItem, ...]:
        try:
            view: dict[str, Any] = json.load(stdout)
            if view["store"] != "repo":
                raise TicketTreeError("the ticket tree must use BMad's repo store")
            items: dict[str, WorkItem] = {}
            for row in cast("list[dict[str, Any]]", view["ready_to_start"]):
                ref, title = row["ref"], row["title"]
                if (
                    not isinstance(ref, str)
                    or not ref
                    or not isinstance(title, str)
                    or ref in items
                ):
                    raise ValueError("invalid or duplicate work item")
                items[ref] = WorkItem(ref, title)
            return tuple(items.values())
        except (KeyError, TypeError, ValueError) as exc:
            raise TicketTreeError("the BMad reader returned no ready-to-start view") from exc


async def read_ticket_tree(
    installation: Path, tree: Path, project_root: Path
) -> tuple[WorkItem, ...]:
    return await BmadTicketTree(tree, installation, project_root=project_root).ready_items()


async def read_ticket_body(
    installation: Path, tree: Path, project_root: Path, ref: str
) -> str | None:
    return await BmadTicketTree(tree, installation, project_root=project_root).body(ref)
