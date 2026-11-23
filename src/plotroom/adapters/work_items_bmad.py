"""Read the ready-to-start view of a local BMad repo ticket tree."""

import asyncio
import contextlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, cast

from plotroom.ports.work_items import WorkItem

_READER_TIMEOUT = 15.0


class TicketTreeError(ValueError):
    """The local source could not produce a ready-to-start view."""


class BmadTicketTree:
    """Use the installed BMad reader so dependencies and plan state follow its rules.

    The runner comes from a trusted BMad installation selected by the daemon operator.
    Ticket files themselves are inputs to the read-only next command.
    """

    def __init__(self, tree: Path, installation: Path) -> None:
        self.tree = tree
        self.installation = installation
        self.reader = installation / "_bmad" / "method" / "scripts" / "tickets.py"

    async def ready_items(self) -> tuple[WorkItem, ...]:
        if not self.tree.is_dir():
            raise TicketTreeError("configured ticket_tree directory does not exist")
        root = next(
            (p for p in (self.tree, *self.tree.parents) if (p / "_bmad").is_dir()), self.tree
        )
        reader = self.reader.resolve()
        if reader.is_relative_to(root) or not reader.is_file():
            raise TicketTreeError("configure a trusted BMad reader outside the mirrored checkout")
        with tempfile.TemporaryFile() as stdout:
            await self._read(reader, root, stdout)
            stdout.seek(0)
            return self._parse(stdout)

    async def _read(self, reader: Path, root: Path, stdout: BinaryIO) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-B",
                str(reader),
                "--project-root",
                str(root),
                "next",
                str(self.tree),
                stdout=stdout,
                stderr=asyncio.subprocess.DEVNULL,
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
        if process.returncode != 0:
            if process.returncode == 2:
                raise TicketTreeError("the ticket tree must use BMad's repo store")
            raise TicketTreeError("the BMad reader could not read the ticket tree")

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
