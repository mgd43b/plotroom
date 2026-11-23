"""Record inbound events and the actionable items they produce."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from plotroom.core.events import AppendResult, FieldSpec, LedgerError, NewEvent
from plotroom.core.lanes import infer_lane

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork
    from plotroom.ports.session_reader import SessionEvent

INBOUND_MESSAGE = "inbound_message"
TURN_REPORT = "claude.turn_report"
SESSION_MESSAGE = "claude.session_message"
SESSION_FIELD = FieldSpec("session_id", "str")
TURN_FIELDS = (
    SESSION_FIELD,
    FieldSpec("report_digest", "str"),
    FieldSpec("turn_index", "int", required=False),
)
CLAUDE_APPENDERS = frozenset({"system", "orchestrator", "clerk", "worker"})


async def append_inbound(uow: UnitOfWork, event: NewEvent) -> AppendResult:
    result = await uow.append(event)
    if result.created:
        await uow.raise_item(
            INBOUND_MESSAGE,
            result.event.id,
            "orchestrator",
            {
                "kind": event.kind,
                "lane_id": event.lane_id,
                "fields": event.fields,
                "body": event.body,
            },
        )
    return result


async def record_claude(
    uow: UnitOfWork,
    report: SessionEvent,
    *,
    lane: str | None = None,
    session_worker: str | None = None,
) -> AppendResult:
    role, _, caller = uow.role.partition(":")
    if role not in CLAUDE_APPENDERS or report.kind not in (TURN_REPORT, SESSION_MESSAGE):
        raise LedgerError("forbidden", "ingest accepts only Claude reports and session messages")
    if not report.session_id.strip():
        raise LedgerError("invalid", "session_id must be nonempty")
    if role == "worker":
        if session_worker is not None and session_worker != caller:
            raise LedgerError("forbidden", "the Claude session is bound to another worker")
        lane = await infer_lane(uow, worker=caller)
        if lane is None:
            raise LedgerError("forbidden", "a worker must have a current lane")
    elif lane is None and session_worker is not None:
        lane = await infer_lane(uow, worker=session_worker)
    elif lane is not None and await uow.find_lane(lane) is None:
        raise LedgerError("not_found", "lane not found")

    fields: dict[str, Any] = {"session_id": report.session_id}
    if report.kind == SESSION_MESSAGE:
        if not report.upstream_id or report.turn_index is not None:
            raise LedgerError("invalid", "a session message needs upstream_id and no turn_index")
        uid = json.dumps([report.session_id, report.upstream_id], separators=(",", ":"))
        result = await append_inbound(
            uow, NewEvent("claude", SESSION_MESSAGE, uid, fields, report.body, lane)
        )
        if role == "worker" and result.event.lane_id != lane:
            raise LedgerError("forbidden", "the message is already recorded on another lane")
        return result
    if report.upstream_id is not None:
        raise LedgerError("invalid", "a turn report derives its upstream_id from the report")
    digest = hashlib.sha256(report.body.encode()).hexdigest()
    aliases = [f"digest:{digest}"]
    fields["report_digest"] = digest
    if report.turn_index is not None:
        if type(report.turn_index) is not int or report.turn_index < 1:
            raise LedgerError("invalid", "turn_index must be a positive integer")
        fields["turn_index"] = report.turn_index
        aliases.insert(0, f"index:{report.turn_index}")
    existing = await uow.find_claude_turn(report.session_id, tuple(aliases))
    if existing is not None:
        result = AppendResult(existing, False)
    else:
        uid = json.dumps([report.session_id, aliases[0]], separators=(",", ":"))
        result = await append_inbound(
            uow, NewEvent("claude", TURN_REPORT, uid, fields, report.body, lane)
        )
    if role == "worker" and result.event.lane_id != lane:
        raise LedgerError("forbidden", "the turn is already recorded on another lane")
    await uow.bind_claude_turn(report.session_id, tuple(aliases), result.event.id)
    return result
