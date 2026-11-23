"""Worker-owned run observations."""

import json
from typing import TYPE_CHECKING, Any

from plotroom.core.events import FieldSpec, LedgerError, NewEvent

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

RUN_STATUS = "run.status"
RUN_ID = FieldSpec("run_id", "str")
RUN_STATE = FieldSpec("state", "str")
RUN_FIELDS = (RUN_ID, RUN_STATE, FieldSpec("finished", "bool"))
FINISHED = frozenset({"completed", "succeeded", "failed", "canceled"})
STATES = FINISHED | {"queued", "running", "in_progress"}


def validate_identity(value: str, name: str) -> None:
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise LedgerError("invalid", f"{name} must be valid UTF-8") from None
    if not value.strip() or "\0" in value or size > 256:
        raise LedgerError("invalid", f"{name} must be 1-256 UTF-8 bytes and contain no NUL")


async def run_report(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    lane: str = args["lane"]
    run_id: str = args["run_id"]
    state: str = args["state"]
    validate_identity(run_id, "run_id")
    if state not in STATES:
        raise LedgerError("invalid", f"run state must be one of {', '.join(sorted(STATES))}")
    current = await uow.find_lane(lane)
    if current is None or current.state == "archived":
        raise LedgerError("conflict", "a run report requires a current lane")
    previous = await uow.latest_event(RUN_STATUS, fields={"run_id": run_id})
    if previous is not None and previous.lane_id != lane:
        raise LedgerError("conflict", "run_id already belongs to another lane")
    result = await uow.append(
        NewEvent(
            "run",
            RUN_STATUS,
            json.dumps([lane, run_id, state], separators=(",", ":")),
            {"run_id": run_id, "state": state, "finished": state in FINISHED},
            lane_id=lane,
        )
    )
    return {"event_id": result.event.id}
