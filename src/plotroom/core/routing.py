"""Project-local assignment of hands-on action kinds to lanes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from plotroom.core.events import FieldSpec, LedgerError
from plotroom.core.next_items import ItemKind

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

ACTION_KIND = FieldSpec("action_kind", "str")
MISSING_ROUTE = ItemKind("route_missing", 20, True, ("Assign a lane to this action kind",))


def validate_route_label(value: object, field: str) -> str:
    error = f"{field} must be a non-blank string of at most 128 UTF-8 bytes without NUL"
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise LedgerError("invalid", error)
    try:
        if len(value.encode()) > 128:
            raise LedgerError("invalid", error)
    except UnicodeEncodeError as exc:
        raise LedgerError("invalid", error) from exc
    return value


async def seed_routes(uow: UnitOfWork, routes: Mapping[str, str]) -> None:
    """Import the configuration as a whole on the first startup with routes."""
    if await uow.has_routes():
        return
    for action_kind, lane in routes.items():
        await uow.save_route(action_kind, lane)
        await uow.clear_item(MISSING_ROUTE.name, action_kind)


async def route(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    action_kind = validate_route_label(args["action_kind"], "action_kind")
    lane = validate_route_label(args["lane"], "lane")
    await uow.save_route(action_kind, lane)
    await uow.clear_item(MISSING_ROUTE.name, action_kind)
    return {"action_kind": action_kind, "lane": lane}


async def route_request(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    action_kind = validate_route_label(args["action_kind"], "action_kind")
    lane = await uow.find_route(action_kind)
    if lane is not None:
        return {"action_kind": action_kind, "lane": lane}
    item = await uow.raise_item(
        MISSING_ROUTE.name, action_kind, "orchestrator", {"action_kind": action_kind}
    )
    return {"action_kind": action_kind, "lane": None, "item": item.id}
