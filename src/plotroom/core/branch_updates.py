"""Fixture branch updates staged and reconciled in the project writer."""

from dataclasses import dataclass
from datetime import datetime

from plotroom.core.actions import BRANCH_UPDATE, Action, finish_action, record_attempt
from plotroom.core.events import FieldSpec, NewEvent
from plotroom.core.lanes import Lane
from plotroom.core.merge_gate import (
    STALE_CI,
    GatePolicy,
    GateSnapshot,
    current_label_evidence,
    determine_main_verdict,
    evaluate_gate,
)
from plotroom.core.merge_lane import lane_matches_branch
from plotroom.core.merge_overlap import ALLOW_OVERLAP
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.github_write import BranchUpdateReceipt

BRANCH_UPDATE_STAGED = "core.branch_update_staged"
BRANCH_UPDATED = "core.branch_updated"
BRANCH_UPDATE_CONFLICT = "branch_update_conflict"
BRANCH_UPDATE_FIELDS = (
    FieldSpec("repository", "str"),
    FieldSpec("number", "int"),
    FieldSpec("head_sha", "str"),
)

BRANCH_UPDATED_FIELDS = (*BRANCH_UPDATE_FIELDS, FieldSpec("main_sha", "str"))


@dataclass(frozen=True)
class BranchUpdate:
    repository: str
    number: int
    branch: str
    head_sha: str
    main_sha: str

    @property
    def key(self) -> str:
        return f"{BRANCH_UPDATE}:{self.number}:{self.head_sha}"


def eligible_branch_update(
    snapshot: GateSnapshot, policy: GatePolicy, now: datetime
) -> BranchUpdate | None:
    pull = snapshot.pull
    labels = current_label_evidence(snapshot, policy, now)
    if (
        pull is None
        or pull.draft is not False
        or pull.head_repository != pull.repository
        or snapshot.earlier_pull is not None
        or snapshot.ambiguous_lane
        or labels is None
        or (
            ALLOW_OVERLAP in labels.attributes["labels"]
            and ALLOW_OVERLAP not in labels.attributes["label_provenance"]
        )
    ):
        return None
    verdict = evaluate_gate(snapshot, policy, now)
    if not verdict.reasons or verdict.reasons[0].code != STALE_CI:
        return None
    main = determine_main_verdict(snapshot, policy, now)
    if main.kind != "green" or main.head_sha is None:
        return None
    return BranchUpdate(pull.repository, pull.number, pull.branch, pull.head_sha, main.head_sha)


async def stage_branch_update(uow: UnitOfWork, update: BranchUpdate) -> bool:
    payload = {
        "repository": update.repository,
        "number": update.number,
        "head_sha": update.head_sha,
    }
    cause = await uow.append(NewEvent("core", BRANCH_UPDATE_STAGED, update.key, payload))
    if not cause.created:
        return False
    await uow.enqueue_action(
        cause.event, BRANCH_UPDATE, {**payload, "branch": update.branch}, stable_key=update.key
    )
    return True


async def _conflict_lane(uow: UnitOfWork, action: Action, branch: str | None) -> Lane | None:
    if branch is None:
        return None
    exact = await uow.branch_lanes(branch)
    if exact:
        return exact[0] if len(exact) == 1 else None

    repository = action.payload["repository"]
    pull = await uow.pull_request(repository, action.payload["number"])
    if (
        pull is None
        or not pull.complete
        or pull.head_repository != repository
        or pull.head_sha != action.payload["head_sha"]
        or pull.branch != branch
    ):
        return None  # A recovered receipt may belong to a different PR head.
    matches = [lane for lane in await uow.active_lanes() if lane_matches_branch(lane, branch)]
    return matches[0] if len(matches) == 1 else None


async def complete_branch_update(
    uow: UnitOfWork,
    action: Action,
    receipt: BranchUpdateReceipt,
    branch: str | None,
    *,
    recovery_only: bool,
) -> None:
    # After a crash, a fixture receipt can be the first durable indication of a write.
    attempted = action if recovery_only and action.attempts else await record_attempt(uow, action)
    if receipt.outcome == "conflict":
        lane = await _conflict_lane(uow, action, branch)
        role = f"worker:{lane.worker}" if lane is not None else "orchestrator"
        await uow.raise_item(
            BRANCH_UPDATE_CONFLICT,
            action.key,
            role,
            {
                **action.payload,
                "main_sha": receipt.main_sha,
                "lane": lane.id if lane is not None else None,
            },
        )
    else:
        fields = {key: action.payload[key] for key in ("repository", "number", "head_sha")}
        await uow.append(
            NewEvent(
                "core",
                BRANCH_UPDATED,
                action.key,
                {**fields, "main_sha": receipt.main_sha},
                f"BRANCH UPDATED #{fields['number']}",
            )
        )
    await finish_action(uow, attempted, "done")
