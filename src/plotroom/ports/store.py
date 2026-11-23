"""The ``Store`` port: what core needs from a project's storage."""

from __future__ import annotations

from typing import Protocol


class Store(Protocol):
    """One project's ledger storage (AD-4: a store is opened for exactly one project)."""

    @property
    def project_id(self) -> str: ...

    async def revision(self) -> str | None:
        """The schema revision the project file is at, or ``None`` for an unmigrated file."""
        ...

    async def close(self) -> None: ...
