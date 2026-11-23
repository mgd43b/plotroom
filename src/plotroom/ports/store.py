"""The ``Store`` port: what core needs from a project's storage."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from plotroom.core.events import Event


class Transaction(Protocol):
    """One open writer transaction (AD-14): all of it commits or rolls back together."""

    async def find_event(self, source: str, kind: str, upstream_id: str) -> Event | None:
        """The stored event with this identity, or ``None``."""
        ...

    async def insert_event(self, event: Event) -> None:
        """Store a new event whose ``seq`` came from ``allocate_seq`` in this transaction."""
        ...

    async def allocate_seq(self) -> int:
        """The project's next integer ``seq`` (AD-9). Rolled back with the transaction."""
        ...


class Store(Protocol):
    """One project's ledger storage (AD-4: a store is opened for exactly one project)."""

    @property
    def project_id(self) -> str: ...

    async def revision(self) -> str | None:
        """The schema revision the project file is at, or ``None`` for an unmigrated file."""
        ...

    def transaction(self) -> AbstractAsyncContextManager[Transaction]:
        """Open the project's one writer transaction.

        Commits when the block exits normally and rolls back on any exception. Only the project
        writer calls this (AD-3); a second concurrent entry raises ``RuntimeError``.
        """
        ...

    async def head_seq(self) -> int:
        """The last allocated ``seq`` (0 when nothing has been written). Reads committed state."""
        ...

    async def events_after(self, seq: int, limit: int = 100) -> list[Event]:
        """Committed events above ``seq``, ascending, at most ``limit``; none when it is below 1."""
        ...

    async def close(self) -> None: ...
