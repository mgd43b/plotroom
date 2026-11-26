"""Check runs by commit and workflow attempts on the default branch."""

from collections.abc import Collection
from dataclasses import replace
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from plotroom.adapters.github import GitHubRateLimited, GitHubUnavailable
from plotroom.adapters.github.junit import JUnitReader
from plotroom.adapters.github.read import (
    GitHubNotFound,
    GitHubReadClient,
    Row,
    number,
    obj,
    rows,
    string,
)
from plotroom.core.github_projection import MAIN_CI_WINDOW, GitHubRecord, GitHubResource
from plotroom.core.merge_flakes import (
    has_test_identity,
    is_failure,
    prior_check_failures,
    same_head_recovery,
)

MAX_EXCERPT = 500
MAX_FAILURE_HISTORY = 100
JUNIT_MISS_REFRESHES = 4
PENDING_WORKFLOW_STATUSES = frozenset(
    {"expected", "in_progress", "pending", "queued", "requested", "waiting"}
)
TEST_PASSED = "plotroom:test-outcome=passed"


def _status(row: Row) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": number(row["id"]),
        "name": string(row["name"]),
        "status": string(row["status"]),
    }
    if row["conclusion"] is not None:
        fields["conclusion"] = string(row["conclusion"])
    return fields


def _state(fields: Row) -> str:
    return f"{fields['status']}/{fields.get('conclusion', '')}"


async def fetch_checks(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    sha: str,
    previous: GitHubResource | None = None,
    *,
    include_junit: bool = True,
    recovery_check_ids: Collection[int] = (),
) -> GitHubResource:
    checks: list[GitHubRecord] = []
    junit = JUnitReader(api, client, token) if include_junit else None
    junit_pending: list[tuple[GitHubRecord, GitHubRecord | None, str, bool]] = []
    prior = (
        {
            (record.fields["id"], record.details.get("completed_at")): record
            for record in previous.records
        }
        if previous and previous.complete
        else {}
    )
    async for row in api.pages(
        client,
        token,
        f"/commits/{quote(sha, safe='')}/check-runs",
        key="check_runs",
        params={"filter": "all"},
        verify_total=True,
    ):
        fields: Row = {**_status(row), "head_sha": sha}
        if row.get("head_sha", sha) != sha:
            raise ValueError("GitHub returned a check for a different head")
        pulls = [number(pull["number"]) for pull in rows(row.get("pull_requests", []))]
        details: Row = {"pull_numbers": pulls}
        if row.get("html_url"):
            details["run_url"] = string(row["html_url"])
        app = obj(row["app"]) if row.get("app") is not None else None
        if app is not None:
            details["app_id"] = number(app["id"])
        if row.get("check_suite") is not None:
            details["suite_id"] = number(obj(row["check_suite"])["id"])
        if row.get("completed_at") is not None:
            timestamp = datetime.fromisoformat(string(row["completed_at"]))
            if timestamp.utcoffset() is None:
                raise ValueError("check completion time has no offset")
            details["completed_at"] = timestamp.timestamp()
        check = GitHubRecord(
            str(fields["id"]), "check_run", fields, details=details, fact=_state(fields)
        )
        passed = fields["status"] == "completed" and fields.get("conclusion") == "success"
        if (is_failure(check) or passed) and row.get("output") is not None:
            count = obj(row["output"]).get("annotations_count")
            cached = prior.get((fields["id"], details.get("completed_at")))
            evidence = "passed_tests" if passed else "annotations"
            # A known zero is an evidence result. An unavailable, malformed or
            # truncated annotation read leaves the field unset and cannot fall back.
            if type(count) is int and 0 <= count <= 300:
                if (
                    cached
                    and cached.details.get("annotation_count") == count
                    and not cached.details.get("junit_evidence")
                    and evidence in cached.details
                ):
                    details[evidence] = cached.details[evidence]
                else:
                    try:
                        details[evidence] = await _annotations(
                            api, client, token, fields["id"], count, passed=passed
                        )
                    except GitHubRateLimited:
                        raise
                    except GitHubUnavailable, httpx.RequestError, KeyError, ValueError, TypeError:
                        # The check verdict is readable even if its evidence was unavailable.
                        pass
                if evidence in details:
                    details["annotation_count"] = count
                if (
                    junit is not None
                    and evidence in details
                    and not any(
                        has_test_identity(entry["path"], entry["test_id"])
                        for entry in details[evidence]
                    )
                    and app is not None
                    and app.get("slug") == "github-actions"
                    and "suite_id" in details
                    and row.get("head_sha") == sha
                ):
                    junit_pending.append((check, cached, evidence, passed))
        checks.append(check)
    if len({check.key for check in checks}) != len(checks):
        raise ValueError("GitHub returned duplicate check IDs")
    if junit_pending and junit is not None:
        failures = [
            *(prior_check_failures(previous) if previous is not None else ()),
            *(check for check in checks if is_failure(check)),
        ]
        for check, cached, evidence, passed in junit_pending:
            if (
                not passed
                or check.fields["id"] in recovery_check_ids
                or any(same_head_recovery(failure, check) for failure in failures)
            ):
                await _read_junit(junit, check, cached, evidence, passed=passed)
    history = dict(previous.attributes.get("prior_failures", {})) if previous else {}
    for check in checks:
        if is_failure(check):
            # On a failed evidence read, replace the old occurrence with the incomplete
            # observation. Only a complete current list may use this history.
            history[check.key] = {"fields": check.fields, "details": check.details}
    history = dict(
        sorted(history.items(), key=lambda item: item[1]["fields"]["id"])[-MAX_FAILURE_HISTORY:]
    )
    return GitHubResource(tuple(checks), attributes={"prior_failures": history})


async def _read_junit(
    reader: JUnitReader,
    check: GitHubRecord,
    cached: GitHubRecord | None,
    evidence: str,
    *,
    passed: bool,
) -> None:
    details = check.details
    if (
        cached is not None
        and cached.fields == check.fields
        and details.get("completed_at") is not None
        and all(
            cached.details.get(key) == details[key]
            for key in ("app_id", "suite_id", "annotation_count")
        )
    ):
        if cached.details.get("junit_evidence") is True and evidence in cached.details:
            details[evidence] = cached.details[evidence]
            details["junit_evidence"] = True
            return
        remaining = cached.details.get("junit_miss_remaining")
        if type(remaining) is int and 0 < remaining <= JUNIT_MISS_REFRESHES:
            details["junit_miss_remaining"] = remaining - 1
            return
    try:
        report = await reader.evidence(check.fields, details["suite_id"], passed=passed)
    except GitHubRateLimited:
        raise
    except (
        GitHubUnavailable,
        httpx.RequestError,
        httpx.InvalidURL,
        TimeoutError,
        KeyError,
        ValueError,
        TypeError,
    ):
        # An uncertain report must not be interpreted as an empty annotation set.
        del details[evidence]
        return
    if report is None:
        # Recheck after a few polls so a late upload still becomes evidence.
        details["junit_miss_remaining"] = JUNIT_MISS_REFRESHES
    else:
        details[evidence] = report
        details["junit_evidence"] = True


async def _annotations(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    check_id: int,
    count: int,
    *,
    passed: bool,
) -> list[Row]:
    if not count:
        return []
    annotations = [
        row async for row in api.pages(client, token, f"/check-runs/{check_id}/annotations")
    ]
    if len(annotations) != count:
        raise ValueError("incomplete check run annotations")
    evidence: list[Row] = []
    for row in annotations:
        level = string(row["annotation_level"])
        expected = (
            level == "notice" and row.get("message") == TEST_PASSED
            if passed
            else level == "failure"
        )
        if expected:
            entry = {
                "path": string(row["path"]),
                "test_id": string(row["title"]) if row.get("title") else "",
            }
            if not passed:
                entry["excerpt"] = string(row["message"])[:MAX_EXCERPT]
            evidence.append(entry)
    return evidence


def comparison_paths(files: Any) -> set[str]:
    paths: set[str] = set()
    for row in rows(files):
        paths.add(string(row["filename"]))
        if "previous_filename" in row:
            paths.add(string(row["previous_filename"]))
        elif row["status"] == "renamed":
            raise ValueError("missing previous path in commit comparison")
    if "" in paths:
        raise ValueError("empty path in commit comparison")
    return paths


async def fetch_comparison(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, base: str, head: str
) -> GitHubResource:
    # Comparisons cap their file list at 300. An exact-cap result cannot prove absence.
    # Bound the work for intervening commits and inspect each to catch reverted edits.
    pair = f"{quote(base, safe='')}...{quote(head, safe='')}"
    result = obj(await api.get(client, token, f"/compare/{pair}", {"page": 1, "per_page": 10}))
    commits = rows(result["commits"])
    if (
        result["status"] != "ahead"
        or obj(result["base_commit"])["sha"] != base
        or obj(result["merge_base_commit"])["sha"] != base
        or not 1 <= len(commits) <= 10
        or result["total_commits"] != len(commits)
        or commits[-1]["sha"] != head
        or len(result["files"]) >= 300
    ):
        return GitHubResource((), complete=False)
    paths = comparison_paths(result["files"])
    if len(commits) > 1:
        for commit in commits:
            sha = string(commit["sha"])
            files: list[Row] = []
            async for file in api.pages(
                client,
                token,
                f"/commits/{quote(sha, safe='')}",
                key="files",
                verify_terminal=True,
            ):
                files.append(file)
                if len(files) >= 300:
                    return GitHubResource((), complete=False)
            paths.update(comparison_paths(files))
    return GitHubResource((), attributes={"paths": sorted(paths)})


async def fetch_main(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    image_jobs: tuple[str, ...],
    previous: GitHubResource | None,
) -> GitHubResource:
    branch, sha = await fetch_default_branch_head(api, client, token)
    old = (
        {row.key: row for row in previous.records}
        if previous and previous.attributes.get("branch") == branch
        else {}
    )
    runs: dict[str, GitHubRecord] = {}
    async for row in api.pages(
        client, token, "/actions/runs", key="workflow_runs", params={"branch": branch}, recent=True
    ):
        fields = _status(row)
        attempt = number(row["run_attempt"])
        run_id = fields["id"]
        key = f"{run_id}/{attempt}"
        cached = old.get(key)
        main_branch_run = _main_run_attribution(row, api.repository.lower(), branch) is True
        if (
            cached is not None
            and fields["status"] == "completed"
            and cached.fact == _state(fields)
            and cached.details.get("workflow_id") == row.get("workflow_id")
            and cached.details.get("main_branch_run") is main_branch_run
        ):
            runs[key] = cached
        else:
            runs[key] = await _workflow_run(api, client, token, row, main_branch_run)
        preceding = f"{run_id}/{attempt - 1}"
        if attempt > 1 and fields["status"] != "completed" and preceding not in old:
            try:
                earlier = obj(
                    await api.get(client, token, f"/actions/runs/{run_id}/attempts/{attempt - 1}")
                )
            except GitHubNotFound:
                continue
            if number(earlier["id"]) != run_id or number(earlier["run_attempt"]) != attempt - 1:
                raise ValueError("GitHub returned a different workflow attempt")
            earlier_on_main = _main_run_attribution(earlier, api.repository.lower(), branch)
            runs[preceding] = await _workflow_run(
                api, client, token, earlier, earlier_on_main is True
            )
    # A rerun replaces the provider's list row for the previous attempt. Keep a bounded
    # recent history so a pending rerun cannot erase the previous completed verdict.
    merged = sorted(
        {**old, **runs}.values(),
        key=lambda run: (run.details["created_at"], run.fields["id"], run.fields["attempt"]),
        reverse=True,
    )
    projected = tuple(
        replace(
            run,
            details={
                **run.details,
                "image_jobs": [job for job in run.fields["jobs"] if job["name"] in image_jobs],
            },
        )
        for run in merged[:100]
    )
    return GitHubResource(projected, attributes={"branch": branch, "head_sha": sha})


async def _workflow_run(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, row: Row, main_branch_run: bool
) -> GitHubRecord:
    jobs = await _workflow_jobs(api, client, token, number(row["id"]), number(row["run_attempt"]))
    record = _workflow_record(row, jobs)
    return replace(record, details={**record.details, "main_branch_run": main_branch_run})


async def fetch_default_branch_head(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str
) -> tuple[str, str]:
    metadata = obj(await api.get(client, token, ""))
    branch = string(metadata["default_branch"])
    branch_tip = obj(await api.get(client, token, f"/branches/{quote(branch, safe='')}"))
    return branch, string(obj(branch_tip["commit"])["sha"])


async def fetch_main_ci(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    workflow: str,
    previous: GitHubResource | None,
    main: GitHubResource | None,
) -> GitHubResource:
    branch, sha = (
        (main.attributes["branch"], main.attributes["head_sha"])
        if main is not None
        else await fetch_default_branch_head(api, client, token)
    )
    if not branch or not sha:
        raise ValueError("GitHub returned an invalid default branch")
    repository = api.repository.lower()
    old = (
        {row.key: row for row in previous.records}
        if previous is not None
        and previous.complete
        and previous.attributes.get("branch") == branch
        and previous.attributes.get("workflow") == workflow
        else {}
    )
    records: dict[str, GitHubRecord] = {}
    workflow_ids: set[int] = set()
    seen_runs: set[int] = set()
    async for row in api.pages(
        client,
        token,
        f"/actions/workflows/{quote(workflow, safe='')}/runs",
        key="workflow_runs",
        params={"branch": branch},
        verify_total=True,
    ):
        record = _configured_run(row, repository, branch)
        if record is None:
            continue
        run_id, attempt = number(row["id"]), number(row["run_attempt"])
        workflow_id = number(row["workflow_id"])
        workflow_ids.add(workflow_id)
        if (
            run_id in seen_runs
            or len(workflow_ids) != 1
            or (workflow.isdecimal() and int(workflow) != workflow_id)
        ):
            raise ValueError("GitHub returned inconsistent workflow evidence")
        seen_runs.add(run_id)
        if record.fields["status"] != "completed":
            if attempt == 1:
                continue
            # The list replaces a run's last attempt as soon as its rerun is queued.
            # Recover the prior completed attempt before considering any older run.
            attempt -= 1
            earlier = old.get(f"{run_id}/{attempt}")
            if not _same_completed_attempt(earlier, record, attempt):
                prior = obj(
                    await api.get(client, token, f"/actions/runs/{run_id}/attempts/{attempt}")
                )
                earlier = _configured_run(prior, repository, branch)
            if earlier is None or not _same_completed_attempt(earlier, record, attempt):
                raise ValueError("GitHub could not establish the prior completed workflow attempt")
            record = earlier
        key = record.key
        conclusion = record.fields["conclusion"]
        if conclusion in {"success", "startup_failure"}:
            records[key] = record
            break
        cached = old.get(key)
        if (
            cached is not None
            and cached.fact == record.fact
            and _same_completed_attempt(cached, record, attempt)
            and cached.details.get("jobs_complete") is True
        ):
            record = cached
        elif conclusion not in {"cancelled", "skipped"}:
            try:
                jobs = await _workflow_jobs(api, client, token, run_id, attempt, verify_total=True)
            except GitHubRateLimited:
                raise
            except GitHubUnavailable, httpx.RequestError, KeyError, TypeError, ValueError:
                record = replace(record, details={**record.details, "jobs_complete": False})
            else:
                record = replace(
                    record,
                    fields={**record.fields, "jobs": jobs},
                    details={**record.details, "jobs_complete": True},
                )
        records[key] = record
        if len(records) == MAIN_CI_WINDOW:
            break
    return GitHubResource(
        tuple(records.values()),
        attributes={"branch": branch, "head_sha": sha, "workflow": workflow},
    )


def _main_run_attribution(row: Row, repository: str, branch: str) -> bool | None:
    event = row.get("event")
    if isinstance(event, str) and (event.startswith("pull_request") or event == "merge_group"):
        return False
    observed_branch = row.get("head_branch")
    if isinstance(observed_branch, str) and observed_branch and observed_branch != branch:
        return False
    repositories: list[str | None] = []
    for key in ("repository", "head_repository"):
        value = row.get(key)
        name = obj(value).get("full_name") if isinstance(value, dict) else None
        repositories.append(name.lower() if isinstance(name, str) and name else None)
    if any(name is not None and name != repository for name in repositories):
        return False
    if (
        not isinstance(event, str)
        or not event.isascii()
        or not event.isidentifier()
        or event != event.lower()
        or observed_branch != branch
        or None in repositories
    ):
        return None
    return True


def _configured_run(row: Row, repository: str, branch: str) -> GitHubRecord | None:
    attribution = _main_run_attribution(row, repository, branch)
    if attribution is False:
        return None
    if attribution is None or not string(row["head_sha"]):
        raise ValueError("GitHub could not establish the workflow run's repository and trigger")
    status, conclusion = string(row["status"]), row["conclusion"]
    if not (
        isinstance(conclusion, str) and bool(conclusion)
        if status == "completed"
        else status in PENDING_WORKFLOW_STATUSES and conclusion is None
    ):
        raise ValueError("GitHub returned invalid workflow attempt status")
    record = _workflow_record(row, [])
    return replace(
        record,
        details={
            **record.details,
            "repository": repository,
            "event": row["event"],
            "main_branch_run": True,
        },
    )


def _same_completed_attempt(
    earlier: GitHubRecord | None, current: GitHubRecord, attempt: int
) -> bool:
    return (
        earlier is not None
        and earlier.fields.get("status") == "completed"
        and isinstance(earlier.fields.get("conclusion"), str)
        and bool(earlier.fields["conclusion"])
        and earlier.fields.get("attempt") == attempt
        and earlier.fields.get("id") == current.fields["id"]
        and earlier.fields.get("head_sha") == current.fields["head_sha"]
        and type(earlier.details.get("workflow_id")) is int
        and earlier.details["workflow_id"] == current.details["workflow_id"]
        and earlier.details.get("repository") == current.details["repository"]
        and earlier.details.get("event") == current.details["event"]
    )


async def _workflow_jobs(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    run_id: int,
    attempt: int,
    *,
    verify_total: bool = False,
) -> list[Row]:
    jobs: list[Row] = []
    async for job in api.pages(
        client,
        token,
        f"/actions/runs/{run_id}/attempts/{attempt}/jobs",
        key="jobs",
        verify_total=verify_total,
    ):
        projected = _status(job)
        if job.get("check_run_url"):
            url = string(job["check_run_url"])
            prefix = f"{api.endpoint}/check-runs/"
            if url.startswith(prefix) and url.removeprefix(prefix).isascii():
                check_id = url.removeprefix(prefix)
                if check_id.isdecimal() and len(check_id) < 20:
                    projected["check_run_id"] = number(int(check_id))
        jobs.append(projected)
    if len({job["id"] for job in jobs}) != len(jobs):
        raise ValueError("GitHub returned duplicate workflow jobs")
    return jobs


def _workflow_record(row: Row, jobs: list[Row]) -> GitHubRecord:
    fields = _status(row)
    run_id, attempt = fields["id"], number(row["run_attempt"])
    fields.update({"head_sha": string(row["head_sha"]), "attempt": attempt, "jobs": jobs})
    details: Row = {"created_at": string(row["created_at"])}
    if row.get("workflow_id") is not None:
        details["workflow_id"] = number(row["workflow_id"])
    return GitHubRecord(
        f"{run_id}/{attempt}", "workflow_run", fields, details=details, fact=_state(fields)
    )
