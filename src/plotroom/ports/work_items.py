"""A read-only source of ready work (AD-17)."""

from dataclasses import dataclass
from typing import Protocol

MAX_WORK_ITEM_BODY_BYTES = 512 * 1024


@dataclass(frozen=True)
class WorkItem:
    ref: str
    title: str


class WorkItemSource(Protocol):
    async def ready_items(self) -> tuple[WorkItem, ...]:
        """Read ready work in source order, or raise if the source cannot be read."""
        ...

    async def body(self, ref: str) -> str | None:
        """Read the full text of a source reference, or None when it is absent."""
        ...
