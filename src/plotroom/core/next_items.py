"""Ranked, durable next items and their lifecycle (AD-18)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from plotroom.core.events import LedgerError
from plotroom.core.roles import OwnershipView

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class ItemKind:
    name: str
    rank_class: int
    state_backed: bool
    suggested_actions: tuple[str, ...]


@dataclass(frozen=True)
class Item:
    id: str
    kind: str
    subject_ref: str
    dedup_key: str
    role: str
    rank_class: int
    state: Literal["open", "snoozed", "closed"]
    opened_seq: int
    closed_seq: int | None
    seq: int
    context: dict[str, Any]
    suggested_actions: tuple[str, ...]


def visible(item: Item, role: str) -> bool:
    return item.role == role or role == "orchestrator"


async def owns_ack(view: OwnershipView, args: dict[str, Any]) -> bool:
    item = await view.find_item(args["item"])
    return item is not None and visible(item, view.role)


async def ack(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    item = await uow.find_item(args["item"])
    if item is None:
        raise LedgerError("not_found", "next item not found")
    if item.state != "open":
        return {"item": item.id, "state": item.state, "changed": False}
    if uow.item_kind(item.kind).state_backed:
        updated = replace(item, state="snoozed", seq=await uow.allocate_seq())
    else:
        seq = await uow.allocate_seq()
        updated = replace(item, state="closed", seq=seq, closed_seq=seq)
    await uow.save_item(updated)
    return {"item": item.id, "state": updated.state, "changed": True}
