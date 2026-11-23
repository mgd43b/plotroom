"""Cause-derived actions committed with their originating unit of work (AD-14)."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from plotroom.core.events import FieldSpec, LedgerError, NewEvent

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

FAILED_ACTION = "failed_action"
BRANCH_UPDATE = "branch_update"
CONTROLLER_MERGE = "controller_merge"
SWITCHBOARD_SEND = "switchboard.send"
ACTION_FIELDS = (FieldSpec("action_key", "str"), FieldSpec("attempt", "int"))
ACTION_PAGE_SIZE = 32


@dataclass(frozen=True)
class Action:
    key: str
    kind: str
    cause_id: str
    payload: dict[str, Any]
    attempts: int = 0
    state: str = "pending"


@dataclass(frozen=True)
class QueuedAction:
    position: int
    action: Action


async def record_attempt(uow: UnitOfWork, action: Action) -> Action:
    attempted = replace(action, attempts=action.attempts + 1)
    await uow.append(
        NewEvent(
            "core",
            "core.action_attempt",
            f"{action.key}:{attempted.attempts}",
            {"action_key": action.key, "attempt": attempted.attempts},
        )
    )
    await uow.update_action(attempted)
    return attempted


async def finish_action(uow: UnitOfWork, action: Action, state: str) -> None:
    if state not in {"done", "failed"}:
        raise LedgerError("invalid", "invalid action result")
    await uow.append(
        NewEvent(
            "core",
            f"core.action_{state}",
            action.key,
            {"action_key": action.key, "attempt": action.attempts},
        )
    )
    await uow.update_action(replace(action, state=state))
    if state == "failed":
        await uow.raise_item(
            FAILED_ACTION,
            action.key,
            "orchestrator",
            {"action_key": action.key, "kind": action.kind, "attempts": action.attempts},
        )
