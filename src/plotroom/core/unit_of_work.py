"""The unit of work (AD-14): the handle given to one input's work inside the writer transaction.

Everything done through it commits or rolls back together. It is the extension point for later
consequences (adapter cursors, next items, triggers, actions).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from plotroom.core.events import (
    AppendResult,
    Event,
    EventRegistry,
    NewEvent,
    format_time,
    validate_fields,
)
from plotroom.ports.clock import Clock
from plotroom.ports.store import Transaction


def new_event_id() -> str:
    return f"evt_{uuid.uuid7()}"


class UnitOfWork:
    """One input's transaction, its role and the services its work may use.

    ``role`` is who the input comes from. ``append`` checks events against ``append_role``, which
    is the same role for a plain event input and ``system`` inside a command (core's own handling).
    """

    def __init__(
        self,
        tx: Transaction,
        events: EventRegistry,
        clock: Clock,
        *,
        role: str,
        append_role: str | None = None,
        id_factory: Callable[[], str] = new_event_id,
    ) -> None:
        self._tx = tx
        self._events = events
        self._clock = clock
        self._role = role
        self._append_role = append_role if append_role is not None else role
        self._id_factory = id_factory

    @property
    def role(self) -> str:
        return self._role

    async def find_event(self, source: str, kind: str, upstream_id: str) -> Event | None:
        return await self._tx.find_event(source, kind, upstream_id)

    async def allocate_seq(self) -> int:
        """The project's next ``seq``; rolled back with the transaction."""
        return await self._tx.allocate_seq()

    async def append(self, event: NewEvent) -> AppendResult:
        """Validate, deduplicate, number and store ``event``.

        A duplicate of ``(source, kind, upstream_id)`` returns the stored event with
        ``created=False`` and touches nothing: no ``seq`` is consumed. The identity is checked
        first, the duplicate lookup next, and the declared fields only for a new event.
        """
        registered = self._events.validate_identity(self._append_role, event)
        existing = await self._tx.find_event(event.source, event.kind, event.upstream_id)
        if existing is not None:
            return AppendResult(existing, False)
        validate_fields(registered.fields, event.fields, event.kind)
        stored = Event(
            seq=await self._tx.allocate_seq(),
            id=self._id_factory(),
            source=event.source,
            kind=event.kind,
            upstream_id=event.upstream_id,
            lane_id=event.lane_id,
            fields=event.fields,
            body=event.body,
            observed_at=format_time(self._clock.now()),
        )
        await self._tx.insert_event(stored)
        return AppendResult(stored, True)
