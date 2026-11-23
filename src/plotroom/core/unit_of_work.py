"""The unit of work (AD-14): the handle given to one input's work inside the writer transaction.

Everything done through it commits or rolls back together. It is the extension point for later
consequences (adapter cursors, next items, triggers, actions).
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from plotroom.core.actions import Action
from plotroom.core.events import (
    AppendResult,
    Event,
    EventRegistry,
    LedgerError,
    NewEvent,
    format_time,
    validate_fields,
)
from plotroom.core.lanes import Lane
from plotroom.core.next_items import Item, ItemKind
from plotroom.core.roles import role_family
from plotroom.ports.clock import Clock
from plotroom.ports.store import Transaction
from plotroom.ports.work_items import WorkItem


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
        items: Mapping[str, ItemKind] | None = None,
        append_role: str | None = None,
        id_factory: Callable[[], str] = new_event_id,
    ) -> None:
        self._tx = tx
        self._events = events
        self._clock = clock
        self._role = role
        self._items: Mapping[str, ItemKind] = items if items is not None else {}
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

    async def ready_work_items(self) -> tuple[WorkItem, ...]:
        return await self._tx.ready_work_items()

    async def replace_work_items(self, items: tuple[WorkItem, ...]) -> None:
        await self._tx.replace_work_items(items)

    async def claimed_work_items(self) -> set[str]:
        return await self._tx.claimed_work_items()

    async def find_lane(self, lane_id: str) -> Lane | None:
        return await self._tx.find_lane(lane_id)

    async def worker_lane(self, worker: str) -> Lane | None:
        return await self._tx.worker_lane(worker)

    async def save_lane(self, lane: Lane) -> None:
        await self._tx.save_lane(lane)

    async def enqueue_action(
        self, cause: Event, kind: str, payload: dict[str, Any], *, discriminator: str = ""
    ) -> str:
        """Queue an effect in its causing transaction. Reusing a key preserves the first effect."""
        if not kind or ":" in kind:
            raise LedgerError("invalid", "action kind must be nonempty and contain no colon")
        key = f"{cause.id}:{kind}:{discriminator}"
        await self._tx.insert_action(Action(key, kind, cause.id, payload))
        return key

    async def update_action(self, action: Action) -> None:
        if self._append_role != "system":
            raise LedgerError("forbidden", "only core may update a committed action")
        await self._tx.update_action(action)

    def item_kind(self, kind: str) -> ItemKind:
        if kind not in self._items:
            raise LedgerError("invalid", f"unknown next item kind {kind!r}")
        return self._items[kind]

    async def find_item(self, item_id: str) -> Item | None:
        return await self._tx.find_item(item_id)

    async def save_item(self, item: Item) -> None:
        await self._tx.save_item(item)

    async def raise_item(
        self, kind: str, subject_ref: str, role: str, context: dict[str, Any]
    ) -> Item:
        """Open an occurrence or update the active one when its condition changes."""
        registered = self.item_kind(kind)
        role_family(role)
        key = f"{kind}:{subject_ref}"
        current = await self._tx.active_item(key)
        if current is not None:
            if not registered.state_backed or current.context == context:
                return current
            item = replace(current, state="open", context=context, seq=await self.allocate_seq())
        else:
            seq = await self.allocate_seq()
            item = Item(
                f"item_{seq}",
                kind,
                subject_ref,
                key,
                role,
                registered.rank_class,
                "open",
                seq,
                None,
                seq,
                context,
                registered.suggested_actions,
            )
        await self.save_item(item)
        return item

    async def active_item_subjects(self, kind: str) -> set[str]:
        return await self._tx.active_item_subjects(kind)

    async def clear_item(self, kind: str, subject_ref: str) -> None:
        if not self.item_kind(kind).state_backed:
            raise LedgerError("invalid", f"{kind!r} is not state-backed")
        if (item := await self._tx.active_item(f"{kind}:{subject_ref}")) is not None:
            seq = await self.allocate_seq()
            await self.save_item(replace(item, state="closed", closed_seq=seq, seq=seq))

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
        # One copy is validated and stored: the caller owns event.fields and may change it while
        # this input awaits the store.
        fields = copy.deepcopy(event.fields)
        validate_fields(registered.fields, fields, event.kind)
        stored = Event(
            seq=await self._tx.allocate_seq(),
            id=self._id_factory(),
            source=event.source,
            kind=event.kind,
            upstream_id=event.upstream_id,
            lane_id=event.lane_id,
            fields=fields,
            body=event.body,
            observed_at=format_time(self._clock.now()),
        )
        await self._tx.insert_event(stored)
        return AppendResult(stored, True)
