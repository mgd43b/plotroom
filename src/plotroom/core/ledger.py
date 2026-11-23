"""The ledger: the one write path into a project's event log (AD-2, AD-3, AD-14)."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from plotroom.core.catalog import Catalog
from plotroom.core.commands import COMMAND_APPENDER, command_kind
from plotroom.core.events import (
    AppendResult,
    Event,
    LedgerError,
    NewEvent,
    is_json,
    validate_fields,
)
from plotroom.core.roles import role_family
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.core.writer import ProjectWriter
from plotroom.ports.clock import Clock
from plotroom.ports.store import Store, Transaction


@dataclass(frozen=True)
class CommandResult:
    """What a command returned; ``replayed`` is true when it is the first call's stored result."""

    result: dict[str, Any]
    replayed: bool


def _check_role(role: str) -> None:
    try:
        role_family(role)
    except ValueError as exc:
        raise LedgerError("invalid", str(exc)) from None


class Ledger:
    """One project's event log: typed appends and deduplicated commands, one transaction each."""

    def __init__(self, store: Store, catalog: Catalog, clock: Clock) -> None:
        self._store = store
        self._catalog = catalog
        self._clock = clock
        self._writer = ProjectWriter(store)

    @property
    def project_id(self) -> str:
        return self._store.project_id

    def start(self) -> None:
        self._writer.start()

    async def stop(self) -> None:
        """Finish the queued inputs and stop the writer; later inputs fail ``unavailable``."""
        await self._writer.stop()

    async def run[T](self, role: str, work: Callable[[UnitOfWork], Awaitable[T]]) -> T:
        """Run ``work`` as one input in one transaction; its appends are checked as ``role``."""
        _check_role(role)

        async def in_transaction(tx: Transaction) -> T:
            return await work(UnitOfWork(tx, self._catalog.events, self._clock, role=role))

        return await self._writer.submit(in_transaction)

    async def append(self, role: str, event: NewEvent) -> AppendResult:
        """Append one event as ``role``. A duplicate returns the stored event, ``created=False``."""
        _check_role(role)
        self._catalog.events.validate_identity(role, event)  # refuse before queueing
        return await self.run(role, lambda uow: uow.append(event))

    async def execute(
        self, role: str, command: str, request_id: str, args: dict[str, Any]
    ) -> CommandResult:
        """Run a mutating command once per ``(role, command, request_id)``.

        The first call runs the handler and records a ``core.<command>`` event with its arguments
        and result in the same transaction. A retry returns that result and runs nothing.
        A failed handler leaves nothing behind and the error reaches the caller.
        """
        _check_role(role)
        registered = self._catalog.commands.get(command)
        if registered is None:
            raise LedgerError("invalid", f"unknown command {command!r}")
        if not isinstance(request_id, str) or not request_id:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise LedgerError("invalid", f"{command}: request_id must be a non-empty string")
        if not isinstance(args, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise LedgerError("invalid", f"{command}: args must be an object")
        # One snapshot is validated, copied to the handler and recorded in the receipt, so a caller
        # mutating its dict while the handler runs changes none of the three.
        snapshot = copy.deepcopy(args)
        kind = command_kind(command)
        upstream_id = f"{role}:{request_id}"

        async def in_transaction(tx: Transaction) -> CommandResult:
            uow = UnitOfWork(
                tx, self._catalog.events, self._clock, role=role, append_role=COMMAND_APPENDER
            )
            recorded = await uow.find_event("core", kind, upstream_id)
            if recorded is not None:
                return CommandResult(recorded.fields["result"], True)
            validate_fields(registered.args, snapshot, command)  # a retry never gets here
            result = await registered.handler(uow, copy.deepcopy(snapshot))
            if not isinstance(result, dict) or not is_json(result):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise TypeError(f"command {command!r} must return a JSON object")
            await uow.append(
                NewEvent(
                    source="core",
                    kind=kind,
                    upstream_id=upstream_id,
                    fields={
                        "role": role,
                        "request_id": request_id,
                        "args": copy.deepcopy(snapshot),
                        "result": result,
                    },
                )
            )
            return CommandResult(result, False)

        return await self._writer.submit(in_transaction)

    async def head_seq(self) -> int:
        """The last allocated ``seq``. Reads committed state, not through the writer."""
        return await self._store.head_seq()

    async def events_after(self, seq: int, limit: int = 100) -> list[Event]:
        """Committed events above ``seq``, ascending."""
        return await self._store.events_after(seq, limit)
