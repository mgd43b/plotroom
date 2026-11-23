"""Check runs by commit and workflow attempts on the default branch."""

from dataclasses import replace
from typing import Any
from urllib.parse import quote

import httpx

from plotroom.adapters.github.read import GitHubReadClient, Row, number, obj, rows, string
from plotroom.core.github_projection import GitHubRecord, GitHubResource


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
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, sha: str
) -> GitHubResource:
    checks: list[GitHubRecord] = []
    async for row in api.pages(
        client,
        token,
        f"/commits/{quote(sha, safe='')}/check-runs",
        key="check_runs",
        params={"filter": "all"},
    ):
        fields = {**_status(row), "head_sha": sha}
        pulls = [number(pull["number"]) for pull in rows(row.get("pull_requests", []))]
        checks.append(
            GitHubRecord(
                str(fields["id"]),
                "check_run",
                fields,
                details={"pull_numbers": pulls},
                fact=_state(fields),
            )
        )
    return GitHubResource(tuple(checks))


async def fetch_main(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    image_jobs: tuple[str, ...],
    previous: GitHubResource | None,
) -> GitHubResource:
    metadata = obj(await api.get(client, token, ""))
    branch = string(metadata["default_branch"])
    branch_tip = obj(await api.get(client, token, f"/branches/{quote(branch, safe='')}"))
    sha = string(obj(branch_tip["commit"])["sha"])
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
        if cached is not None and fields["status"] == "completed" and cached.fact == _state(fields):
            runs[key] = cached
            continue
        jobs = [
            _status(job)
            async for job in api.pages(
                client, token, f"/actions/runs/{run_id}/attempts/{attempt}/jobs", key="jobs"
            )
        ]
        fields.update({"head_sha": string(row["head_sha"]), "attempt": attempt, "jobs": jobs})
        details = {
            "created_at": string(row["created_at"]),
        }
        runs[key] = GitHubRecord(key, "workflow_run", fields, details=details, fact=_state(fields))
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
