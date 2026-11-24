"""Switchboard ingress and writer-owned leases for a remote bridge."""

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from plotroom.core.actions import SWITCHBOARD_SEND, finish_action, record_attempt
from plotroom.core.budgets import BUDGET_ITEM
from plotroom.core.events import LedgerError, NewEvent, format_time
from plotroom.core.intake import append_inbound
from plotroom.core.lanes import infer_lane
from plotroom.core.outbox import LEASE_DURATION
from plotroom.core.trigger_actions import DeliveryRoute

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork


async def record_inbound(
    uow: UnitOfWork, board: str, senders: Mapping[str, str], message_id: int, sender: str, body: str
) -> dict[str, Any]:
    if message_id < 1 or not sender:
        raise LedgerError("invalid", "invalid Switchboard message identity")
    try:
        sender.encode("utf-8")
        body.encode("utf-8")
    except UnicodeEncodeError:
        raise LedgerError("invalid", "Switchboard text must be valid UTF-8") from None
    worker = senders.get(sender)
    lane = await infer_lane(uow, worker=worker)
    fields = {"sender": sender}
    if worker is not None:
        fields["worker"] = worker
    result = await append_inbound(
        uow,
        NewEvent("switchboard", "switchboard.message", f"{board}:{message_id}", fields, body, lane),
    )
    return {"event": result.event.id}


async def receive_bridge(
    uow: UnitOfWork, args: dict[str, Any], *, board: str, senders: Mapping[str, str]
) -> dict[str, Any]:
    if args["board"] != board:
        raise LedgerError("forbidden", "the bridge is scoped to another board")
    return await record_inbound(
        uow, board, senders, args["message_id"], args["sender"], args["body"]
    )


async def lease_bridge(
    uow: UnitOfWork,
    args: dict[str, Any],
    *,
    routes: Mapping[str, DeliveryRoute],
    board: str,
    action_attempts: int,
) -> dict[str, Any]:
    if args["board"] != board:
        raise LedgerError("forbidden", "the bridge is scoped to another board")
    workers = tuple(routes)
    now = uow.now()
    while (action := await uow.bridge_action(workers, format_time(now))) is not None:
        if action.attempts >= action_attempts:
            await finish_action(uow, action, "failed")
            continue
        if action.kind == SWITCHBOARD_SEND:
            address, body = action.payload["to"], action.payload["body"]
        else:
            worker = action.payload["worker"]
            if action.key.startswith(f"{BUDGET_ITEM.name}:"):
                await finish_action(
                    uow,
                    action,
                    "failed",
                    failure_reason="the remote bridge cannot fence this send to its original lane",
                )
                continue
            address, body = routes[worker].destination, action.payload["text"]
        act_id = f"bridge_{uuid4()}"
        expires = format_time(now + LEASE_DURATION)
        # Lease metadata travels with the existing action row, under the project's writer lock.
        await record_attempt(
            uow,
            replace(
                action,
                payload={**action.payload, "_bridge_lease": {"id": act_id, "expires": expires}},
            ),
        )
        return {
            "lease": {
                "act_id": act_id,
                "action_key": action.key,
                "to": address,
                "body": body,
                "expires_at": expires,
                "lease_seconds": LEASE_DURATION.total_seconds(),
            }
        }
    return {"lease": None}


async def complete_bridge(
    uow: UnitOfWork, args: dict[str, Any], *, workers: tuple[str, ...]
) -> dict[str, Any]:
    action = await uow.bridge_action(workers, format_time(uow.now()), args["act_id"])
    if action is None:
        raise LedgerError("conflict", "act_id does not identify a current bridge lease")
    payload = {key: value for key, value in action.payload.items() if key != "_bridge_lease"}
    await finish_action(uow, replace(action, payload=payload), "done")
    return {"action_key": action.key, "state": "done"}
