"""Typed deployment facts from GitHub and deploy-lane workers."""

import json
import re
from typing import TYPE_CHECKING, Any

from plotroom.core.events import FieldSpec, LedgerError, NewEvent

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

DEPLOY_STATUS = "deploy.status"
DEPLOY_FINISHED_STATES = frozenset({"success", "failure", "error", "inactive"})
DEPLOY_STATES = DEPLOY_FINISHED_STATES | {"pending", "queued", "in_progress"}
DEPLOY_ARGS = (FieldSpec("environment", "str"), FieldSpec("sha", "str"), FieldSpec("state", "str"))
DEPLOY_FIELDS = (
    *DEPLOY_ARGS,
    FieldSpec("finished", "bool"),
    FieldSpec("repository", "str", required=False),
    FieldSpec("deployment_id", "int", required=False),
    FieldSpec("status_id", "int", required=False),
)
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def validate_deploy_environment(value: str) -> str:
    if not value or value != value.strip() or len(value) > 255 or not value.isprintable():
        raise ValueError("environment must be an exact, non-empty name of at most 255 characters")
    return value


def validate_report_sha(value: str) -> str:
    if not _SHA.fullmatch(value):
        raise ValueError("sha must be a full lowercase hexadecimal commit SHA")
    return value


def deploy_fields(environment: str, sha: str, state: str) -> dict[str, Any]:
    if state not in DEPLOY_STATES:
        raise ValueError("unsupported deployment state")
    return {
        "environment": validate_deploy_environment(environment),
        "sha": validate_report_sha(sha),
        "state": state,
        "finished": state in DEPLOY_FINISHED_STATES,
    }


async def deploy_report(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    lane_id: str = args["lane"]
    lane = await uow.find_lane(lane_id)
    if lane is None or lane.state == "archived":
        raise LedgerError("conflict", "lane is archived or missing")
    try:
        fields = deploy_fields(args["environment"], args["sha"], args["state"])
    except ValueError as exc:
        raise LedgerError("invalid", str(exc)) from None
    identity = json.dumps([lane_id, fields["environment"], fields["sha"], fields["state"]])
    event = await uow.append(
        NewEvent("deploy", DEPLOY_STATUS, f"worker:{identity}", fields, lane_id=lane_id)
    )
    return {"event": event.event.id, "created": event.created}
