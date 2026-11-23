"""Check runs by commit and workflow attempts on the default branch."""

from dataclasses import replace
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from plotroom.adapters.github import GitHubRateLimited, GitHubUnavailable
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
from plotroom.core.merge_flakes import is_failure

MAX_EXCERPT = 500
MAX_FAILURE_HISTORY = 100
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
) -> GitHubResource:
    checks: list[GitHubRecord] = []
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
    ):
        fields: Row = {**_status(row), "head_sha": sha}
        if row.get("head_sha", sha) != sha:
            raise ValueError("GitHub returned a check for a different head")
        pulls = [number(pull["number"]) for pull in rows(row.get("pull_requests", []))]
        details: Row = {"pull_numbers": pulls}
        if row.get("html_url"):
            details["run_url"] = string(row["html_url"])
        if row.get("app") is not None:
            details["app_id"] = number(obj(row["app"])["id"])
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
        checks.append(check)
    if len({check.key for check in checks}) != len(checks):
        raise ValueError("GitHub returned duplicate check IDs")
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
                client, token, f"/commits/{quote(sha, safe='')}", key="files"
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
    branch, sha = await _main_head(api, client, token)
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
        if (
            cached is not None
            and fields["status"] == "completed"
            and cached.fact == _state(fields)
            and cached.details.get("workflow_id") == row.get("workflow_id")
        ):
            runs[key] = cached
        else:
            runs[key] = await _workflow_run(api, client, token, row)
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
            runs[preceding] = await _workflow_run(api, client, token, earlier)
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
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, row: Row
) -> GitHubRecord:
    jobs = await _workflow_jobs(api, client, token, number(row["id"]), number(row["run_attempt"]))
    return _workflow_record(row, jobs)


async def _main_head(
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
        else await _main_head(api, client, token)
    )
    if not branch or not sha:
        raise ValueError("GitHub returned an invalid default branch")
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
    async for row in api.pages(
        client,
        token,
        f"/actions/workflows/{quote(workflow, safe='')}/runs",
        key="workflow_runs",
        params={"branch": branch, "status": "completed"},
        verify_total=True,
    ):
        run_id, attempt = number(row["id"]), number(row["run_attempt"])
        key = f"{run_id}/{attempt}"
        workflow_id = number(row["workflow_id"])
        workflow_ids.add(workflow_id)
        if (
            row["status"] != "completed"
            or row["head_branch"] != branch
            or not string(row["head_sha"])
            or not string(row["conclusion"])
            or key in records
            or len(workflow_ids) != 1
            or (workflow.isdecimal() and int(workflow) != workflow_id)
        ):
            raise ValueError("GitHub returned inconsistent workflow evidence")
        record = _workflow_record(row, [])
        conclusion = row["conclusion"]
        if conclusion in {"success", "startup_failure"}:
            records[key] = record
            break
        cached = old.get(key)
        if (
            cached is not None
            and cached.fact == record.fact
            and cached.fields["head_sha"] == record.fields["head_sha"]
            and cached.details["workflow_id"] == workflow_id
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
                record = _workflow_record(row, jobs)
                record = replace(record, details={**record.details, "jobs_complete": True})
        records[key] = record
        if len(records) == MAIN_CI_WINDOW:
            break
    return GitHubResource(
        tuple(records.values()),
        attributes={"branch": branch, "head_sha": sha, "workflow": workflow},
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
