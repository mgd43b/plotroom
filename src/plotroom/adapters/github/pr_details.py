"""Read review resolution, changed files and label history for a pull request."""

from typing import Any

import httpx

from plotroom.adapters.github.read import GitHubReadClient, number, obj, string
from plotroom.core.github import PullRequest
from plotroom.core.github_projection import GitHubRecord, GitHubResource


async def fetch_threads(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, pr: PullRequest
) -> GitHubResource:
    records: list[GitHubRecord] = []
    async for row in api.review_threads(client, token, pr.number):
        identifier = string(row["id"])
        resolved: Any = row["isResolved"]
        if type(resolved) is not bool:
            raise ValueError("invalid review thread state")
        fields = {
            "id": identifier,
            "number": pr.number,
            "resolved": resolved,
            "path": string(row["path"]),
        }
        if row["line"] is not None:
            fields["line"] = number(row["line"])
        records.append(
            GitHubRecord(identifier, "review_thread", fields, fact=str(resolved).lower())
        )
    return GitHubResource(tuple(records))


async def fetch_files(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, pr: PullRequest
) -> GitHubResource:
    async def confirm_head() -> int:
        current = obj(await api.get(client, token, f"/pulls/{pr.number}"))
        if string(obj(current["head"])["sha"]) != pr.head_sha:
            raise ValueError("PR head moved during file poll")
        count: Any = current["changed_files"]
        if type(count) is not int or not 0 <= count <= 3000:
            raise ValueError("invalid or unbounded PR file list")
        return count

    expected = await confirm_head()
    records: list[GitHubRecord] = []
    async for row in api.pages(client, token, f"/pulls/{pr.number}/files"):
        path = string(row["filename"])
        fields = {
            "number": pr.number,
            "head_sha": pr.head_sha,
            "path": path,
            "status": string(row["status"]),
        }
        if "previous_filename" in row:
            fields["previous_path"] = string(row["previous_filename"])
        records.append(GitHubRecord(path, "pull_file", fields))
    if expected != len({record.key for record in records}) or expected != await confirm_head():
        raise ValueError("incomplete PR file list")
    return GitHubResource(tuple(records), attributes={"head_sha": pr.head_sha})


async def fetch_labels(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, pr: PullRequest
) -> GitHubResource:
    records: list[GitHubRecord] = []
    async for row in api.pages(client, token, f"/issues/{pr.number}/events"):
        action = row.get("event")
        if action not in ("labeled", "unlabeled"):
            continue
        identifier = number(row["id"])
        fields = {
            "number": pr.number,
            "name": string(obj(row["label"])["name"]),
            "action": action,
            "id": identifier,
        }
        if row["actor"] is not None:
            fields["actor"] = string(obj(row["actor"])["login"])
        records.append(GitHubRecord(str(identifier), "pull_label", fields, fact=str(identifier)))
    records.sort(key=lambda record: record.fields["id"])
    active = [
        string(row["name"]) async for row in api.pages(client, token, f"/issues/{pr.number}/labels")
    ]
    actors: dict[str, str | None] = {}
    for record in records:
        name = record.fields["name"]
        if record.fields["action"] == "labeled":
            actors[name] = record.fields.get("actor")
        else:
            actors.pop(name, None)
    return GitHubResource(
        tuple(records), attributes={"labels": {label: actors.get(label) for label in active}}
    )
