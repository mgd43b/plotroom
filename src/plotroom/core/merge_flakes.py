"""Derive durable flake candidates from complete, read-only GitHub evidence."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from plotroom.core.events import Event, FieldSpec, NewEvent
from plotroom.core.github_projection import GitHubRecord, GitHubResource

if TYPE_CHECKING:
    from plotroom.core.github import PullRequest
    from plotroom.core.unit_of_work import UnitOfWork


FLAKE_CANDIDATE = "flake_candidate"
FLAKE_EVENT = "core.flake_candidate"
MAIN_FLAKE_EVENT = "core.main_check_flake"
FLAKE_COMPARISONS = "flake_comparisons"
SOURCE = "core"
FAILURES = {"failure", "timed_out"}
MAX_MAIN_RUNS = 12

FAILURE_FIELDS = (
    FieldSpec("repository", "str"),
    FieldSpec("head_sha", "str"),
    FieldSpec("check_id", "int"),
)
FLAKE_FIELDS = (
    *FAILURE_FIELDS,
    FieldSpec("check_name", "str"),
    FieldSpec("run_url", "str", required=False),
    FieldSpec("pattern", "str"),
    FieldSpec("number", "int", required=False),
    FieldSpec("test_id", "str", required=False),
    FieldSpec("path", "str", required=False),
    FieldSpec("success_head_sha", "str", required=False),
    FieldSpec("success_check_id", "int", required=False),
)


class EventLookup(Protocol):
    async def find_event(self, source: str, kind: str, upstream_id: str) -> Event | None: ...


def is_failure(record: GitHubRecord) -> bool:
    return record.fields["status"] == "completed" and record.fields.get("conclusion") in FAILURES


def _is_success(record: GitHubRecord) -> bool:
    return record.fields["status"] == "completed" and record.fields.get("conclusion") == "success"


def _failure_key(repository: str, sha: str, check_id: int) -> str:
    return f"{repository}:{sha}:{check_id}"


async def main_check_is_flake(
    view: EventLookup, repository: str, head_sha: str, check_id: int
) -> bool:
    return (
        await view.find_event(
            SOURCE, MAIN_FLAKE_EVENT, _failure_key(repository, head_sha, check_id)
        )
        is not None
    )


def _failures(checks: GitHubResource) -> tuple[GitHubRecord, ...]:
    return tuple(
        GitHubRecord(key, "check_run", row["fields"], details=row["details"])
        for key, row in checks.attributes.get("prior_failures", {}).items()
    )


def _same_check(failure: GitHubRecord, success: GitHubRecord) -> bool:
    app = failure.details.get("app_id")
    suite = failure.details.get("suite_id")
    same_head = failure.fields["head_sha"] == success.fields["head_sha"]
    return failure.fields["name"] == success.fields["name"] and (
        failure.key == success.key
        or (
            app is not None
            and app == success.details.get("app_id")
            and (not same_head or (suite is not None and suite == success.details.get("suite_id")))
        )
    )


def _later_check(failure: GitHubRecord, success: GitHubRecord) -> bool:
    earlier = failure.details.get("completed_at")
    later = success.details.get("completed_at")
    if earlier is not None and later is not None:
        return later > earlier or (later == earlier and success.fields["id"] > failure.fields["id"])
    # An identical check ID in the durable prior-failure history also proves an observed
    # failure-to-success transition. Distinct runs need completion times to prove order.
    return success.key == failure.key


def _concrete_path(path: str) -> bool:
    return bool(path) and all(part not in {"", ".", ".."} for part in path.split("/"))


@dataclass(frozen=True)
class Candidate:
    failure: GitHubRecord
    pattern: str
    evidence: dict[str, str] | None
    success: GitHubRecord | None = None
    number: int | None = None


def _candidates(
    failure: GitHubRecord,
    success: GitHubRecord | None,
    *,
    number: int | None = None,
    changed_paths: set[str] | None = None,
) -> tuple[tuple[Candidate, ...], bool]:
    annotations = failure.details.get("annotations")
    if annotations is None:
        return (), False
    same_head = success is not None and failure.fields["head_sha"] == success.fields["head_sha"]
    if same_head:
        pattern = "same_head_pass"
    elif changed_paths is not None:
        pattern = "pr_unchanged_test" if number is not None else "main_unchanged_test_pass"
    else:
        return (), False
    if not annotations:
        return (
            ((Candidate(failure, pattern, None, success, number),), True)
            if same_head and failure.details.get("run_url")
            else ((), False)
        )
    passed_tests = (
        {(test["path"], test["test_id"]) for test in success.details.get("passed_tests", [])}
        if success is not None and not same_head
        else None
    )
    result: list[Candidate] = []
    for annotation in annotations:
        path = annotation["path"]
        if not _concrete_path(path) or not annotation["test_id"]:
            continue
        if not same_head and (changed_paths is None or path in changed_paths):
            continue
        if passed_tests is not None and (path, annotation["test_id"]) not in passed_tests:
            continue
        result.append(Candidate(failure, pattern, annotation, success, number))
    return tuple(result), len(result) == len(annotations)


async def _record_candidates(
    uow: UnitOfWork, repository: str, candidates: tuple[Candidate, ...]
) -> None:
    for candidate in candidates:
        failure = candidate.failure
        head, check_id = failure.fields["head_sha"], failure.fields["id"]
        fields: dict[str, Any] = {
            "repository": repository,
            "head_sha": head,
            "check_id": check_id,
            "check_name": failure.fields["name"],
            "pattern": candidate.pattern,
        }
        if url := failure.details.get("run_url"):
            fields["run_url"] = url
        identity = _failure_key(repository, head, check_id)
        excerpt = ""
        if candidate.evidence is not None:
            # A check-level candidate already represents this failure, including after ack.
            if await uow.find_event(SOURCE, FLAKE_EVENT, identity) is not None:
                continue
            path, test_id = candidate.evidence["path"], candidate.evidence["test_id"]
            test_key = hashlib.sha256(json.dumps([path, test_id]).encode()).hexdigest()
            identity = f"{identity}:{test_key}"
            fields.update(path=path, test_id=test_id)
            excerpt = candidate.evidence["excerpt"]
        if candidate.number is not None:
            fields["number"] = candidate.number
        if candidate.success is not None:
            fields.update(
                success_head_sha=candidate.success.fields["head_sha"],
                success_check_id=candidate.success.fields["id"],
            )
        recorded = await uow.append(NewEvent(SOURCE, FLAKE_EVENT, identity, fields, excerpt))
        if recorded.created:
            await uow.raise_item(FLAKE_CANDIDATE, identity, "clerk", {**fields, "excerpt": excerpt})


async def record_same_head(uow: UnitOfWork, repository: str, checks: GitHubResource) -> None:
    if not checks.complete:
        return
    successes = [check for check in checks.records if _is_success(check)]
    for failure in _failures(checks):
        success = next(
            (
                check
                for check in successes
                if _same_check(failure, check) and _later_check(failure, check)
            ),
            None,
        )
        if success is not None:
            candidates, _ = _candidates(failure, success)
            await _record_candidates(uow, repository, candidates)


async def record_pull_flakes(
    uow: UnitOfWork,
    repository: str,
    pull: PullRequest,
    checks: GitHubResource,
    files: GitHubResource,
) -> None:
    if not checks.complete or not files.complete:
        return
    current = await uow.pull_request(repository, pull.number)
    if (
        current is None
        or not current.complete
        or current.state != "open"
        or (current.head_sha, current.version, current.updated_at)
        != (pull.head_sha, pull.version, pull.updated_at)
        or (current.head_sha, current.version, current.updated_at)
        != (
            files.attributes.get("head_sha"),
            files.attributes.get("pull_version"),
            files.attributes.get("pull_updated_at"),
        )
        or not files.attributes.get("base_ref")
        or not files.attributes.get("base_sha")
    ):
        return
    paths: set[str] = set()
    for record in files.records:
        row = record.fields
        if row["status"] == "renamed" and not row.get("previous_path"):
            return
        paths.update(row[key] for key in ("path", "previous_path") if key in row)
    for failure in checks.records:
        if is_failure(failure) and failure.fields["head_sha"] == pull.head_sha:
            candidates, _ = _candidates(failure, None, number=pull.number, changed_paths=paths)
            await _record_candidates(uow, repository, candidates)


def main_runs(
    main: GitHubResource | None, *, limit: int = MAX_MAIN_RUNS
) -> tuple[GitHubRecord, ...]:
    if main is None or not main.complete:
        return ()
    runs = tuple(
        run
        for run in main.records
        if run.details.get("main_branch_run") is True
        and "workflow_id" in run.details
        and run.fields["status"] == "completed"
        and any("check_run_id" in job for job in run.fields["jobs"])
    )[:limit]
    workflows_with_failed_jobs = {
        run.details["workflow_id"] for run in runs if _unique_job_links(run, FAILURES)
    }
    return tuple(run for run in runs if run.details["workflow_id"] in workflows_with_failed_jobs)


def _unique_job_links(run: GitHubRecord, conclusions: set[str]) -> dict[str, int]:
    jobs: list[dict[str, Any]] = run.fields["jobs"]
    counts = Counter(job["name"] for job in jobs)
    return {
        job["name"]: job["check_run_id"]
        for job in jobs
        if counts[job["name"]] == 1
        and "check_run_id" in job
        and job["status"] == "completed"
        and job.get("conclusion") in conclusions
    }


@dataclass(frozen=True)
class MainPair:
    failure: GitHubRecord
    success: GitHubRecord

    @property
    def base(self) -> str:
        return self.failure.fields["head_sha"]

    @property
    def head(self) -> str:
        return self.success.fields["head_sha"]


def main_pairs(
    runs: tuple[GitHubRecord, ...], checks: dict[str, GitHubResource]
) -> tuple[MainPair, ...]:
    pairs: list[MainPair] = []
    for index, run in enumerate(runs):
        observed = checks.get(run.fields["head_sha"])
        if observed is None or not observed.complete:
            continue
        failures = {row.fields["id"]: row for row in _failures(observed)}
        failed_jobs = _unique_job_links(run, FAILURES)
        for newer in runs[:index]:
            if newer.details["workflow_id"] != run.details["workflow_id"]:
                continue
            passed = checks.get(newer.fields["head_sha"])
            if passed is None or not passed.complete:
                continue
            successes = {
                check.fields["id"]: check for check in passed.records if _is_success(check)
            }
            passed_jobs = _unique_job_links(newer, {"success"})
            for name, identifier in failed_jobs.items():
                failure = failures.get(identifier)
                success = successes.get(passed_jobs.get(name))
                if failure is None or success is None:
                    continue
                if run.fields["head_sha"] != newer.fields["head_sha"] and not failure.details.get(
                    "annotations"
                ):
                    continue
                if _same_check(failure, success) and _later_check(failure, success):
                    pairs.append(MainPair(failure, success))
    return tuple(pairs)


async def record_main_flakes(
    uow: UnitOfWork, repository: str, pair: MainPair, comparison: GitHubResource | None
) -> None:
    paths: set[str] | None = None
    if pair.base != pair.head:
        if comparison is None or not comparison.complete:
            return
        paths = set(comparison.attributes["paths"])
    candidates, fully_proven = _candidates(pair.failure, pair.success, changed_paths=paths)
    await _record_candidates(uow, repository, candidates)
    if fully_proven:
        check_id = pair.failure.fields["id"]
        await uow.append(
            NewEvent(
                SOURCE,
                MAIN_FLAKE_EVENT,
                _failure_key(repository, pair.base, check_id),
                {"repository": repository, "head_sha": pair.base, "check_id": check_id},
            )
        )
