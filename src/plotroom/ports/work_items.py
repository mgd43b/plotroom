"""A read-only source of ready work (AD-17)."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WorkItem:
    ref: str
    title: str


class WorkItemSource(Protocol):
    async def ready_items(self) -> tuple[WorkItem, ...]:
        """Read ready work in source order, or raise if the source cannot be read."""
        ...
