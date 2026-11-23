"""Durable merge decisions and work items derived from a committed project view."""

from dataclasses import asdict, replace
from hashlib import sha256
from shlex import join
from typing import Any

from plotroom.core.actions import CONTROLLER_MERGE
from plotroom.core.branch_updates import eligible_branch_update, stage_branch_update
from plotroom.core.events import FieldSpec, NewEvent
from plotroom.core.github import PullRequest
from plotroom.core.merge_gate import (
    GatePolicy,
    GateSnapshot,
    GateVerdict,
    active_skip_label,
    authorized_label,
    current_label_evidence,
    determine_main_verdict,
    evaluate_gate,
    load_gate_snapshot,
)
from plotroom.core.merge_lane import MergeLaneIndex
from plotroom.core.next_items import ItemKind
from plotroom.core.unit_of_work import UnitOfWork

MERGE_VERDICT = "core.merge_verdict"
MERGE_OBSERVED = "core.merge_observed"
MERGE_OBSERVATION_PENDING = "core.merge_observation_pending"
MERGE_REFUSAL_REPORTED = "core.merge_refusal_reported"
MERGE_REFUSAL_DELIVERED = "core.merge_refusal_delivered"
MERGE_STAGED = "core.merge_staged"
MERGE_READY = "merge_ready"
MERGE_REFUSAL = "merge_refusal"
DUPLICATE_PR = "duplicate_lane_pr"
OUTSIDE_GATE_MERGE = "outside_gate_merge"

PULL_FIELDS = (
    FieldSpec("repository", "str"),
    FieldSpec("number", "int"),
    FieldSpec("head_sha", "str"),
)
MERGE_EVENTS = {
    MERGE_VERDICT: (
        *PULL_FIELDS,
        FieldSpec("verdict", "str"),
        FieldSpec("reasons", "array"),
        FieldSpec("exit_class", "int"),
        FieldSpec("as_of", "str", required=False),
        FieldSpec("supersedes_head_sha", "str", required=False),
    ),
    MERGE_OBSERVED: (
        *PULL_FIELDS,
        FieldSpec("merge_sha", "str"),
        FieldSpec("classification", "str"),
    ),
    MERGE_OBSERVATION_PENDING: (*PULL_FIELDS, FieldSpec("classification", "str")),
    MERGE_REFUSAL_REPORTED: (*PULL_FIELDS, FieldSpec("reason", "object")),
    MERGE_REFUSAL_DELIVERED: (*PULL_FIELDS, FieldSpec("recipient", "str")),
    MERGE_STAGED: (*PULL_FIELDS, FieldSpec("main_sha", "str"), FieldSpec("method", "str")),
}
ACTIVE_MERGE_ITEMS = (MERGE_READY, MERGE_REFUSAL, DUPLICATE_PR)
MERGE_ITEMS = (
    ItemKind(
        MERGE_READY,
        15,
        True,
        (
            "Call merge_gate with both SHA pins. Run the merge command only on MERGE.",
            "The GitHub command enforces the PR head pin.",
        ),
    ),
    ItemKind(MERGE_REFUSAL, 10, True, ("Address the merge gate refusal",)),
    ItemKind(DUPLICATE_PR, 10, True, ("Resolve the overlapping open pull requests",)),
    ItemKind(OUTSIDE_GATE_MERGE, 10, False, ("Inspect the merge observed outside the gate",)),
)
_QUIET_REFUSALS = {"checks_pending", "checks_below_minimum"}


def pull_fields(pull: PullRequest) -> dict[str, Any]:
    return {"repository": pull.repository, "number": pull.number, "head_sha": pull.head_sha}


async def observe_merge(uow: UnitOfWork, pull: PullRequest, *, transition: bool) -> None:
    observation_key = f"{pull.repository}#{pull.number}@{pull.head_sha}"
    pending = await uow.find_event("core", MERGE_OBSERVATION_PENDING, observation_key)
    if not transition and pending is None:
        return  # Historical rows can gain a SHA after the migration or a full reconciliation.

    item = await uow.active_item(MERGE_READY, str(pull.number))
    gated = (
        item is not None
        and item.state == "open"
        and (item.context["repository"], item.context["head_sha"])
        == (pull.repository, pull.head_sha)
    )
    classification = (
        pending.fields["classification"]
        if pending is not None
        else ("gated" if gated else "outside_gate")
    )
    if pull.merge_commit_sha is None:
        await uow.append(
            NewEvent(
                "core",
                MERGE_OBSERVATION_PENDING,
                observation_key,
                {**pull_fields(pull), "classification": classification},
            )
        )
        return

    context = {
        **pull_fields(pull),
        "merge_sha": pull.merge_commit_sha,
        "classification": classification,
    }
    key = f"{pull.repository}#{pull.number}@{pull.merge_commit_sha}"
    receipt = await uow.append(
        NewEvent("core", MERGE_OBSERVED, key, context, f"MERGED #{pull.number} ({classification})")
    )
    if receipt.created and classification == "outside_gate":
        await uow.raise_item(OUTSIDE_GATE_MERGE, key, "orchestrator", context)


async def log_verdict(
    uow: UnitOfWork, pull: PullRequest, verdict: GateVerdict, lane_id: str | None
) -> None:
    reasons = [asdict(reason) for reason in verdict.reasons]
    previous = await uow.latest_merge_verdict(pull.repository, pull.number)
    signature = (verdict.head_sha, verdict.verdict, reasons)
    fields = {
        **pull_fields(pull),
        "verdict": verdict.verdict,
        "reasons": reasons,
        "exit_class": verdict.exit_class,
    }
    if previous is not None:
        prior_head = previous.fields["head_sha"]
        if signature == (prior_head, previous.fields["verdict"], previous.fields["reasons"]):
            return
        if prior_head != verdict.head_sha:
            fields["supersedes_head_sha"] = prior_head
    if verdict.as_of is not None:
        fields["as_of"] = verdict.as_of
    revision = previous.seq if previous is not None else 0
    key = f"{pull.repository}#{pull.number}:{revision}"
    result = await uow.append(
        NewEvent("core", MERGE_VERDICT, key, fields, f"{verdict.verdict} #{pull.number}", lane_id)
    )
    await uow.save_merge_verdict(pull.repository, pull.number, result.event.seq)


async def _merge_item(
    uow: UnitOfWork,
    pull: PullRequest,
    snapshot: GateSnapshot,
    policy: GatePolicy,
    *,
    clerk: bool,
    stage_fixture_merges: bool,
) -> None:
    main = determine_main_verdict(snapshot, policy, uow.now())
    if main.head_sha is None:
        return
    marked_fix = authorized_label(snapshot, policy, uow.now(), policy.fix_label) is not None
    role = "clerk" if clerk and not marked_fix else "orchestrator"
    args = {
        "pr": pull.number,
        "expected_head_sha": pull.head_sha,
        "expected_main_sha": main.head_sha,
    }
    command = join(
        [
            "gh",
            "pr",
            "merge",
            str(pull.number),
            "--repo",
            pull.repository,
            f"--{policy.merge_method}",
            "--match-head-commit",
            pull.head_sha,
        ]
    )
    context = {
        **pull_fields(pull),
        "main_sha": main.head_sha,
        "method": policy.merge_method,
        "merge_gate": args,
        "command": command,
        "command_pin": "head_sha",
    }
    subject = str(pull.number)
    item = await uow.active_item(MERGE_READY, subject)
    if item is not None and (item.context != context or item.role != role):
        await uow.clear_item(MERGE_READY, subject)
    ready = await uow.raise_item(MERGE_READY, subject, role, context)
    if (
        stage_fixture_merges
        and policy.controller_merges is True
        and main.kind == "green"
        and not marked_fix
        and ready.state == "open"
    ):
        effect_key = f"{CONTROLLER_MERGE}:{pull.number}:{pull.head_sha}:{main.head_sha}"
        key = f"{effect_key}:{ready.id}"
        fields = {**pull_fields(pull), "main_sha": main.head_sha, "method": policy.merge_method}
        cause = await uow.append(NewEvent("core", MERGE_STAGED, key, fields))
        if cause.created:
            await uow.enqueue_action(
                cause.event,
                CONTROLLER_MERGE,
                {**fields, "effect_key": effect_key, "item_id": ready.id},
                stable_key=key,
            )


async def _refusal_item(
    uow: UnitOfWork, pull: PullRequest, snapshot: GateSnapshot, verdict: GateVerdict
) -> None:
    lane = snapshot.lane
    subject = str(pull.number)
    reason = verdict.reasons[0]
    if reason.code in _QUIET_REFUSALS or (
        snapshot.checks is None and reason.code in {"checks_unreadable", "pr_unavailable"}
    ):
        await uow.clear_item(MERGE_REFUSAL, subject)
        return
    context = {**pull_fields(pull), "reason": asdict(reason), "lane": lane.id if lane else None}
    current = await uow.active_item(MERGE_REFUSAL, subject)
    role = f"worker:{lane.worker}" if lane else "orchestrator"
    if current is not None:
        if (current.context, current.role) == (context, role):
            return
        await uow.clear_item(MERGE_REFUSAL, subject)
    fingerprint = sha256(reason.detail.encode()).hexdigest()
    key = f"{pull.repository}#{pull.number}@{pull.head_sha}:{reason.code}:{fingerprint}"
    reported = await uow.append(
        NewEvent(
            "core",
            MERGE_REFUSAL_REPORTED,
            key,
            {**pull_fields(pull), "reason": asdict(reason)},
            lane_id=lane.id if lane else None,
        )
    )
    delivered = await uow.append(
        NewEvent(
            "core",
            MERGE_REFUSAL_DELIVERED,
            f"{reported.event.upstream_id}:{role}",
            {**pull_fields(pull), "recipient": role},
            lane_id=lane.id if lane else None,
        )
    )
    if delivered.created:
        await uow.raise_item(MERGE_REFUSAL, subject, role, context)


async def reconcile_merge_controller(
    uow: UnitOfWork,
    repository: str,
    policy: GatePolicy,
    *,
    github_unavailable: bool,
    clerk: bool,
    stage_fixture_merges: bool,
    branch_updates: bool,
) -> None:
    numbers = set(uow.merge_pull_numbers)
    pulls = None
    if uow.all_merge_pulls or uow.merge_check_heads or len(numbers) > 1:
        pulls = await uow.open_pulls(repository)
        numbers.update(
            pull.number
            for pull in pulls
            if uow.all_merge_pulls or pull.head_sha in uow.merge_check_heads
        )
    lane_index = (
        MergeLaneIndex(await uow.active_lanes(), pulls) if pulls is not None and numbers else None
    )
    for number in sorted(uow.merge_observations):
        pull, transition = uow.merge_observations[number]
        if pull.repository == repository:
            await observe_merge(uow, pull, transition=transition)
    for number in sorted(numbers):
        snapshot = replace(
            await load_gate_snapshot(uow, repository, number, lane_index=lane_index),
            github_unavailable=github_unavailable,
        )
        now = uow.now()
        pull = snapshot.pull
        subject = str(number)
        if (
            pull is None
            or pull.state != "open"
            or pull.draft is not False
            or active_skip_label(snapshot, policy, now)
            or (
                policy.skip_label is not None
                and current_label_evidence(snapshot, policy, now) is None
            )
        ):
            for kind in ACTIVE_MERGE_ITEMS:
                await uow.clear_item(kind, subject)
            continue

        earlier = snapshot.earlier_pull
        if earlier is not None:
            await uow.raise_item(
                DUPLICATE_PR,
                subject,
                "orchestrator",
                {
                    **pull_fields(pull),
                    "first_pr": earlier,
                    "lane": snapshot.lane.id if snapshot.lane else None,
                },
            )
        else:
            await uow.clear_item(DUPLICATE_PR, subject)

        verdict = evaluate_gate(snapshot, policy, now)
        await log_verdict(uow, pull, verdict, snapshot.lane.id if snapshot.lane else None)
        if verdict.verdict == "MERGE":
            await uow.clear_item(MERGE_REFUSAL, subject)
            await _merge_item(
                uow,
                pull,
                snapshot,
                policy,
                clerk=clerk,
                stage_fixture_merges=stage_fixture_merges,
            )
        else:
            await uow.clear_item(MERGE_READY, subject)
            await _refusal_item(uow, pull, snapshot, verdict)
            if (
                branch_updates
                and not github_unavailable
                and (update := eligible_branch_update(snapshot, policy, now)) is not None
            ):
                await stage_branch_update(uow, update)
