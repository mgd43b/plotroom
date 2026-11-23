"""Read normalized inbound signals from local session state."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SessionEvent:
    kind: str
    session_id: str
    body: str
    upstream_id: str | None = None
    turn_index: int | None = None


class SessionReader(Protocol):
    async def read(self, session_id: str) -> tuple[SessionEvent, ...]:
        """Return observations after the last accepted checkpoint."""
        ...

    def accept(self, session_id: str) -> None:
        """Advance after the caller has handled the most recent read."""
        ...
