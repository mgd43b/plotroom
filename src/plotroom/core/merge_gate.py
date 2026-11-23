"""Read-only merge readiness rules over a committed GitHub projection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast

from plotroom.core.events import format_time
from plotroom.core.github import PULL_REQUESTS, FetchCursor, PullRequest
from plotroom.core.github_projection import (
    CHECKS,
    LABELS,
    MAIN,
    MAIN_CI,
    MAIN_CI_WINDOW,
    THREADS,
    GitHubRecord,
    GitHubResource,
    unresolved_threads,
)
from plotroom.core.merge_flakes import FAILURES as MAIN_FAILURES
from plotroom.core.merge_flakes import EventLookup, main_check_is_flake
from plotroom.core.merge_overlap import (
    ALLOW_OVERLAP,
    PULL_COMPARISON,
    overlap_label_is_bound,
    pinned_overlap,
)

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork
    from plotroom.ports.store import Transaction

type ExitClass = Literal[0, 1, 2, 3, 4, 5]
type VerdictKind = Literal["MERGE", "REFUSE"]
type MainVerdictKind = Literal["green", "red", "none", "unreadable"]

_PENDING_STATUSES = frozenset(
    {"expected", "in_progress", "pending", "queued", "requested", "waiting"}
)
_PASS_CONCLUSIONS = frozenset({"success", "neutral", "cancelled", "skipped"})
_FAIL_CONCLUSIONS = frozenset({"action_required", "error", "failure", "timed_out"})
_MAIN_JOB_CONCLUSIONS = _PASS_CONCLUSIONS | _FAIL_CONCLUSIONS | {"stale", "startup_failure"}


@dataclass(frozen=True)
class GatePolicy:
    min_checks: int | None = None
    ignore_checks: tuple[str, ...] = ()
    max_age: timedelta = timedelta(minutes=2)
    pull_max_age: timedelta | None = None
    ci_workflow: str | None = None
    fix_label: str | None = None
    authorized_label_logins: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateSnapshot:
    pull: PullRequest | None
    checks: GitHubResource | None
    pull_cursor: FetchCursor | None = None
    github_unavailable: bool = False
    threads: GitHubResource | None = None
    main_ci: GitHubResource | None = None
    labels: GitHubResource | None = None
    main_flakes: frozenset[tuple[str, int]] = frozenset()
    freeze_reason: str | None = None
    main: GitHubResource | None = None
    comparison: GitHubResource | None = None


@dataclass(frozen=True)
class GateReason:
    code: str
    detail: str
    exit_class: ExitClass = 2


@dataclass(frozen=True)
class GateVerdict:
    verdict: VerdictKind
    reasons: tuple[GateReason, ...]
    exit_class: ExitClass
    head_sha: str | None
    as_of: str | None


@dataclass(frozen=True)
class MainVerdict:
    kind: MainVerdictKind
    detail: str
    branch: str | None = None
    head_sha: str | None = None


def observation_time(value: str) -> datetime | None:
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return timestamp if timestamp.utcoffset() is not None else None


def fresh(value: str, now: datetime, max_age: timedelta) -> bool:
    observed = observation_time(value)
    return observed is not None and timedelta() <= now - observed <= max_age


def _projection_reason(
    snapshot: GateSnapshot, policy: GatePolicy, now: datetime
) -> GateReason | None:
    if type(policy.min_checks) is not int or policy.min_checks < 1:
        return GateReason("minimum_checks_unconfigured", "set the minimum to at least one check", 1)
    pull, cursor = snapshot.pull, snapshot.pull_cursor
    if snapshot.github_unavailable or pull is None or not pull.head_sha:
        return GateReason("pr_unavailable", "could not read the pull request head", 1)
    if not pull.complete:
        return GateReason("pr_incomplete", "pull request evidence is incomplete")
    if pull.state != "open":
        return GateReason("pr_not_open", "pull request is not open")
    # The incremental cursor confirms that polling is current. Individual rows may be
    # older until a full reconciliation includes them again.
    pull_max_age = policy.pull_max_age if cursor is not None else None
    if not fresh(pull.as_of, now, pull_max_age or policy.max_age) or (
        cursor is not None and not fresh(cursor.as_of, now, policy.max_age)
    ):
        return GateReason("pr_stale", "pull request evidence is stale")
    return None


def _check_buckets(checks: GitHubResource, head_sha: str) -> list[tuple[str, str]] | None:
    latest: dict[tuple[str, int | None, int | None], GitHubRecord] = {}
    for check in checks.records:
        fields = check.fields
        if (
            check.kind != "check_run"
            or type(fields.get("id")) is not int
            or fields["id"] < 1
            or not isinstance(fields.get("name"), str)
            or not fields["name"]
            or fields.get("head_sha") != head_sha
        ):
            return None
        app_id = check.details.get("app_id")
        # Unidentified runs cannot establish that they supersede one another.
        if type(app_id) is int and app_id > 0:
            key = (fields["name"], app_id, None)
        else:
            key = (fields["name"], None, fields["id"])
        prior = latest.get(key)
        if prior is None or fields["id"] > prior.fields["id"]:
            latest[key] = check
    buckets: list[tuple[str, str]] = []
    for check in latest.values():
        status, conclusion = check.fields.get("status"), check.fields.get("conclusion")
        if not isinstance(status, str) or (
            conclusion is not None and not isinstance(conclusion, str)
        ):
            return None
        if status in _PENDING_STATUSES and conclusion is None:
            bucket = "pending"
        elif status != "completed":
            return None
        elif conclusion == "stale":
            bucket = "pending"
        elif conclusion in _PASS_CONCLUSIONS:
            bucket = "pass"
        elif conclusion in _FAIL_CONCLUSIONS:
            bucket = "fail"
        else:
            return None
        buckets.append((check.fields["name"], bucket))
    return buckets


def _rule_checks(snapshot: GateSnapshot, policy: GatePolicy, now: datetime) -> GateReason | None:
    pull, checks = snapshot.pull, snapshot.checks
    if pull is None or checks is None or not checks.complete:
        return GateReason("checks_unreadable", "could not read the checks")
    if not fresh(checks.as_of, now, policy.max_age):
        return GateReason("checks_stale", "check evidence is stale")
    buckets = _check_buckets(checks, pull.head_sha)
    if buckets is None:
        return GateReason("checks_unreadable", "could not read the checks")
    pending = sum(bucket == "pending" for _, bucket in buckets)
    if pending:
        return GateReason("checks_pending", f"{pending} checks pending")
    minimum = policy.min_checks
    total = len({name for name, _ in buckets})
    if minimum is not None and total < minimum:
        return GateReason(
            "checks_below_minimum",
            f"only {total} checks have reported (at least {minimum} expected)",
        )
    failures = sorted(
        {name for name, bucket in buckets if bucket == "fail" and name not in policy.ignore_checks}
    )
    if failures:
        return GateReason("checks_failing", f"failing: {'; '.join(failures)}")
    return None


def _rule_review_threads(
    snapshot: GateSnapshot, policy: GatePolicy, now: datetime
) -> GateReason | None:
    pull, threads = snapshot.pull, snapshot.threads
    incomplete = GateReason("review_threads_incomplete", "could not read the review threads")
    if (
        pull is None
        or threads is None
        or not threads.complete
        or threads.attributes.get("head_sha") != pull.head_sha
        or type(threads.attributes.get("pull_version")) is not int
        or threads.attributes["pull_version"] != pull.version
        or not fresh(threads.as_of, now, policy.max_age)
    ):
        return incomplete
    if len({thread.key for thread in threads.records}) != len(threads.records) or any(
        thread.kind != "review_thread"
        or not thread.key
        or thread.fields.get("id") != thread.key
        or thread.fields.get("number") != pull.number
        or type(thread.fields.get("resolved")) is not bool
        for thread in threads.records
    ):
        return incomplete
    unresolved = unresolved_threads(threads)
    if unresolved:
        return GateReason("review_threads_unresolved", f"{len(unresolved)} unresolved threads")
    return None


def failed_main_jobs(run: GitHubRecord) -> tuple[int | None, ...] | None:
    """Return failed Check Runs IDs, retaining an unlinked job as an unmatched failure."""
    if run.details.get("jobs_complete") is not True:
        return None
    jobs = run.fields.get("jobs")
    if not isinstance(jobs, list):
        return None
    failed: list[int | None] = []
    for entry in cast("list[Any]", jobs):
        if not isinstance(entry, dict):
            return None
        job = cast("dict[str, Any]", entry)
        conclusion = job.get("conclusion")
        if (
            job.get("status") != "completed"
            or not isinstance(conclusion, str)
            or conclusion not in _MAIN_JOB_CONCLUSIONS
        ):
            return None
        if conclusion in MAIN_FAILURES:
            identifier = job.get("check_run_id")
            failed.append(identifier if type(identifier) is int and identifier > 0 else None)
    linked = [identifier for identifier in failed if identifier is not None]
    if len(set(linked)) != len(linked):
        return None
    return tuple(failed)


async def read_main_flakes(
    view: EventLookup, repository: str, main: GitHubResource | None
) -> frozenset[tuple[str, int]]:
    if main is None or not main.complete:
        return frozenset()
    failures: set[tuple[str, int]] = {
        (head, check_id)
        for run in main.records[:MAIN_CI_WINDOW]
        if isinstance(head := run.fields.get("head_sha"), str) and head
        for check_id in failed_main_jobs(run) or ()
        if check_id is not None
    }
    return frozenset(
        [
            (head, check_id)
            for head, check_id in sorted(failures)
            if await main_check_is_flake(view, repository, head, check_id)
        ]
    )


async def load_gate_snapshot(
    reader: Transaction | UnitOfWork, repository: str | None, number: int
) -> GateSnapshot:
    """Read all gate inputs from one consistent transaction."""
    freeze_reason = await reader.deploy_freeze()
    if repository is None:
        return GateSnapshot(None, None, freeze_reason=freeze_reason)
    pull = await reader.pull_request(repository, number)
    scope = str(number)
    cursor = await reader.fetch_cursor(repository, PULL_REQUESTS)
    checks = (
        await reader.github_resource(repository, CHECKS, pull.head_sha)
        if pull is not None
        else None
    )
    main_ci = await reader.github_resource(repository, MAIN_CI, "")
    return GateSnapshot(
        pull,
        checks,
        cursor,
        threads=await reader.github_resource(repository, THREADS, scope),
        main_ci=main_ci,
        labels=await reader.github_resource(repository, LABELS, scope),
        main_flakes=await read_main_flakes(reader, repository, main_ci),
        freeze_reason=freeze_reason,
        main=await reader.github_resource(repository, MAIN, ""),
        comparison=await reader.github_resource(repository, PULL_COMPARISON, scope),
    )


def determine_main_verdict(
    snapshot: GateSnapshot, policy: GatePolicy, now: datetime
) -> MainVerdict:
    """Read the newest usable run in the configured workflow's completed history."""
    main = snapshot.main_ci
    if policy.ci_workflow is None:
        return MainVerdict("none", "the CI workflow is not configured")
    unreadable = MainVerdict("unreadable", "could not list the CI runs")
    if (
        main is None
        or not main.complete
        or main.attributes.get("workflow") != policy.ci_workflow
        or not fresh(main.as_of, now, policy.max_age)
    ):
        return unreadable
    branch, tip = main.attributes.get("branch"), main.attributes.get("head_sha")
    if not isinstance(branch, str) or not branch or not isinstance(tip, str) or not tip:
        return unreadable
    for run in main.records[:MAIN_CI_WINDOW]:
        head, conclusion = run.fields.get("head_sha"), run.fields.get("conclusion")
        identifier = run.fields.get("id")
        if (
            run.kind != "workflow_run"
            or run.fields.get("status") != "completed"
            or not isinstance(head, str)
            or not head
            or not isinstance(conclusion, str)
            or not conclusion
            or type(identifier) is not int
            or identifier < 1
        ):
            return unreadable
        detail = f"{conclusion} {head[:8]}"
        if conclusion in {"cancelled", "skipped"}:
            continue
        if conclusion == "success":
            return MainVerdict("green", detail, branch, tip)
        if conclusion == "startup_failure":
            return MainVerdict("red", detail, branch, tip)
        failures = failed_main_jobs(run)
        if failures is None:
            return MainVerdict(
                "unreadable",
                f"the jobs of run {identifier} could not be read ({head[:8]})",
                branch,
                tip,
            )
        if any(check is None or (head, check) not in snapshot.main_flakes for check in failures):
            return MainVerdict("red", detail, branch, tip)
    return MainVerdict("none", "no completed CI run with a verdict", branch, tip)


def current_label_evidence(
    snapshot: GateSnapshot, policy: GatePolicy, now: datetime
) -> GitHubResource | None:
    pull, labels = snapshot.pull, snapshot.labels
    if (
        pull is None
        or labels is None
        or not labels.complete
        or not fresh(labels.as_of, now, policy.max_age)
        or labels.attributes.get("head_sha") != pull.head_sha
        or labels.attributes.get("pull_version") != pull.version
        or labels.attributes.get("pull_updated_at") != pull.updated_at
        or not isinstance(labels.attributes.get("labels"), dict)
        or not isinstance(labels.attributes.get("label_provenance"), dict)
    ):
        return None
    return labels


def authorized_label(
    snapshot: GateSnapshot, policy: GatePolicy, now: datetime, label: str | None
) -> int | None:
    labels = current_label_evidence(snapshot, policy, now)
    if label is None or labels is None:
        return None
    active = cast("dict[str, Any]", labels.attributes["labels"])
    provenance = cast("dict[str, Any]", labels.attributes["label_provenance"])
    raw_event = provenance.get(label)
    if not isinstance(raw_event, dict):
        return None
    event = cast("dict[str, Any]", raw_event)
    if event.get("actor_type") != "User":
        return None
    login, event_id = event.get("login"), event.get("event_id")
    if (
        not isinstance(login, str)
        or login != active.get(label)
        or login.lower() not in policy.authorized_label_logins
        or type(event_id) is not int
        or event_id < 1
    ):
        return None
    return event_id


def _rule_main(snapshot: GateSnapshot, policy: GatePolicy, now: datetime) -> GateReason | None:
    verdict = determine_main_verdict(snapshot, policy, now)
    if verdict.kind == "green":
        return None
    branch = verdict.branch or "main"
    if verdict.kind == "red":
        if authorized_label(snapshot, policy, now, policy.fix_label) is not None:
            return None
        return GateReason("main_red", f"{branch}'s latest CI verdict is red: {verdict.detail}", 5)
    return GateReason("main_no_verdict", f"{branch} has no usable CI verdict: {verdict.detail}")


STALE_CI = "stale_ci"


def _rule_overlap(snapshot: GateSnapshot, policy: GatePolicy, now: datetime) -> GateReason | None:
    pull, main, comparison = snapshot.pull, snapshot.main, snapshot.comparison
    verdict = determine_main_verdict(snapshot, policy, now)
    branch, tip = verdict.branch, verdict.head_sha
    unreadable = GateReason(
        "overlap_unreadable", "could not compare the PR with the default branch"
    )
    if (
        pull is None
        or branch is None
        or tip is None
        or main is None
        or not main.complete
        or not fresh(main.as_of, now, policy.max_age)
        or main.attributes.get("branch") != branch
        or main.attributes.get("head_sha") != tip
        or comparison is None
        or not fresh(comparison.as_of, now, policy.max_age)
    ):
        return unreadable
    overlap = pinned_overlap(comparison, pull, branch, tip)
    if overlap is None:
        return unreadable
    merge_base, paths = overlap
    if not paths:
        return None
    event = authorized_label(snapshot, policy, now, ALLOW_OVERLAP)
    if event is not None and overlap_label_is_bound(snapshot.labels, pull, event):
        return None
    detail = (
        f"{branch} changed these PR files since the PR's base {merge_base[:8]} "
        "(stale CI: update the branch and run CI again):"
    )
    return GateReason(STALE_CI, "\n".join((detail, *(f"  {path}" for path in paths))), 3)


type GateRule = Callable[[GateSnapshot, GatePolicy, datetime], GateReason | None]
GATE_RULES: tuple[GateRule, ...] = (_rule_checks, _rule_review_threads, _rule_main, _rule_overlap)


def evaluate_gate(snapshot: GateSnapshot, policy: GatePolicy, now: datetime) -> GateVerdict:
    """Return the first refusal in clerk order and the oldest included observation."""
    pull, cursor, checks = snapshot.pull, snapshot.pull_cursor, snapshot.checks
    head = (pull.head_sha or None) if pull is not None else None
    timestamps = (
        timestamp
        for observation in (
            pull,
            cursor,
            checks,
            snapshot.threads,
            snapshot.main_ci,
            snapshot.labels,
            snapshot.main,
            snapshot.comparison,
        )
        if observation is not None
        if (timestamp := observation_time(observation.as_of)) is not None
    )
    watermark = min(timestamps, default=None)
    as_of = None if watermark is None else format_time(watermark)
    if snapshot.freeze_reason is not None:
        reason = GateReason("deploy_window", snapshot.freeze_reason)
        return GateVerdict("REFUSE", (reason,), reason.exit_class, head, as_of)
    if (reason := _projection_reason(snapshot, policy, now)) is not None:
        return GateVerdict("REFUSE", (reason,), reason.exit_class, head, as_of)
    for rule in GATE_RULES:
        if (reason := rule(snapshot, policy, now)) is not None:
            return GateVerdict("REFUSE", (reason,), reason.exit_class, head, as_of)
    return GateVerdict("MERGE", (), 0, head, as_of)
