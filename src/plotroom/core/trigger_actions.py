"""Validation and dispatch for the closed set of local workflow actions."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from plotroom.core.actions import Action
from plotroom.core.checklists import ARCHIVE_PROPOSAL, propose_lane_archive
from plotroom.core.events import FieldSpec, LedgerError, validate_fields
from plotroom.core.lanes import mark_lane
from plotroom.core.next_items import ItemKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from plotroom.core.unit_of_work import UnitOfWork

NOTIFY = "notify_orchestrator"
SEND = "send"
MARK = "mark"
PROPOSE_ARCHIVE = "propose_archive"
NOTIFICATION_ITEM = ItemKind("trigger_notification", 20, False, ("Read the notification",))
ARCHIVE_ITEM = ItemKind(ARCHIVE_PROPOSAL, 30, True, ("Review the lane for archival",))
MARK_FIELDS = (FieldSpec("lane", "str"), FieldSpec("state", "str"))
MARK_STATES = frozenset({"active", "ready"})
_SCHEMAS = {
    NOTIFY: (FieldSpec("text", "str"),),
    SEND: (FieldSpec("worker", "str"), FieldSpec("text", "str")),
    MARK: MARK_FIELDS,
    PROPOSE_ARCHIVE: (FieldSpec("lane", "str"),),
}


@dataclass(frozen=True)
class DeliveryRoute:
    channel: Literal["switchboard", "claude"]
    destination: str


async def validate_action(
    uow: UnitOfWork, action: dict[str, Any], routes: Mapping[str, DeliveryRoute]
) -> tuple[str, dict[str, Any], str | None]:
    if len(action) != 1:
        raise LedgerError("invalid", "action must name exactly one registered action")
    kind, payload = next(iter(action.items()))
    schema = _SCHEMAS.get(kind)
    if schema is None:
        raise LedgerError("invalid", f"unknown action {kind!r}")
    if not isinstance(payload, dict):
        raise LedgerError("invalid", f"{kind} arguments must be an object")
    args = cast("dict[str, Any]", payload)
    validate_fields(schema, args, kind)
    for value in args.values():
        if not value.strip():
            raise LedgerError("invalid", f"{kind} arguments must be nonempty")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise LedgerError("invalid", f"{kind} arguments must be valid UTF-8") from None
    if kind == MARK and args["state"] not in MARK_STATES:
        raise LedgerError("invalid", "mark state must be active or ready")
    if "lane" in args:
        lane = await uow.find_lane(args["lane"])
        if lane is None or lane.state == "archived":
            raise LedgerError("invalid", "action target must be a current lane")
        return kind, args, lane.id
    if kind == SEND:
        worker = args["worker"]
        if worker not in routes:
            raise LedgerError("invalid", "send target must have a configured delivery channel")
        lane = await uow.worker_lane(worker)
        return kind, args, lane.id if lane else None
    return kind, args, None


async def apply_local_action(uow: UnitOfWork, action: Action, repository: str | None) -> None:
    """Apply an effect within the transaction that records its successful attempt."""
    if action.kind == NOTIFY:
        await uow.raise_item(
            NOTIFICATION_ITEM.name,
            action.key,
            "orchestrator",
            {"text": action.payload["text"], "action_key": action.key},
        )
    elif action.kind == MARK:
        await mark_lane(uow, action, repository=repository)
    elif action.kind == PROPOSE_ARCHIVE:
        lane = await uow.find_lane(action.payload["lane"])
        if lane is not None and lane.state != "archived":
            await propose_lane_archive(uow, lane.id)
