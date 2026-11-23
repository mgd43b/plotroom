"""Read review resolution, changed files, text and label history for a pull request."""

from datetime import datetime
from typing import Any

import httpx

from plotroom.adapters.github.read import GitHubReadClient, Row, number, obj, string
from plotroom.core.github import PullRequest
from plotroom.core.github_projection import GitHubRecord, GitHubResource
from plotroom.core.merge_overlap import full_sha


async def fetch_threads(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, pr: PullRequest
) -> GitHubResource:
    records: list[GitHubRecord] = []
    async for row in api.review_threads(client, token, pr.number, pr.head_sha):
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
    return GitHubResource(
        tuple(records), attributes={"head_sha": pr.head_sha, "pull_version": pr.version}
    )


async def fetch_files(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, pr: PullRequest
) -> GitHubResource:
    async def confirm_pull() -> tuple[str, str, int]:
        current = obj(await api.get(client, token, f"/pulls/{pr.number}"))
        if string(obj(current["head"])["sha"]) != pr.head_sha or datetime.fromisoformat(
            string(current["updated_at"])
        ) != datetime.fromisoformat(pr.updated_at):
            raise ValueError("PR changed during file poll")
        base = obj(current["base"])
        ref, sha = string(base["ref"]), string(base["sha"])
        if not ref or not sha:
            raise ValueError("PR base is unknown during file poll")
        count: Any = current["changed_files"]
        if type(count) is not int or not 0 <= count <= 3000:
            raise ValueError("invalid or unbounded PR file list")
        return ref, sha, count

    base_ref, base_sha, expected = await confirm_pull()
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
    if (base_ref, base_sha, expected) != await confirm_pull():
        raise ValueError("PR base or changed file count moved during file poll")
    if expected != len(records) or expected != len({record.key for record in records}):
        raise ValueError("incomplete PR file list")
    return GitHubResource(
        tuple(records),
        attributes={
            "head_sha": pr.head_sha,
            "base_ref": base_ref,
            "base_sha": base_sha,
            "pull_version": pr.version,
            "pull_updated_at": pr.updated_at,
        },
    )


async def fetch_pull_text(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, pr: PullRequest
) -> GitHubResource:
    path = f"/pulls/{pr.number}"

    async def confirm_pull() -> tuple[str, int]:
        current = obj(await api.get(client, token, path))
        if string(obj(current["head"])["sha"]) != pr.head_sha or datetime.fromisoformat(
            string(current["updated_at"])
        ) != datetime.fromisoformat(pr.updated_at):
            raise ValueError("PR changed during text poll")
        body = current["body"]
        return "" if body is None else string(body), number(current["commits"])

    body, expected = await confirm_pull()
    commits: dict[str, str] = {}
    async for row in api.pages(client, token, f"{path}/commits", verify_terminal=True):
        sha = string(row["sha"])
        if not full_sha(sha) or sha in commits or len(commits) >= expected:
            raise ValueError("invalid PR commit list")
        commits[sha] = string(obj(row["commit"])["message"])
    if (body, expected) != await confirm_pull():
        raise ValueError("PR body or commit count moved during text poll")
    if len(commits) != expected or next(reversed(commits), None) != pr.head_sha:
        raise ValueError("incomplete PR commit list")
    return GitHubResource(
        (),
        attributes={
            "head_sha": pr.head_sha,
            "pull_version": pr.version,
            "pull_updated_at": pr.updated_at,
            "body": body,
            "commit_count": expected,
            "commit_messages": list(commits.values()),
        },
    )


async def fetch_labels(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, pr: PullRequest
) -> GitHubResource:
    active = [
        string(row["name"]) async for row in api.pages(client, token, f"/issues/{pr.number}/labels")
    ]
    records: list[GitHubRecord] = []
    provenance: dict[str, Row] = {}
    async for row in api.pages(client, token, f"/issues/{pr.number}/events", verify_terminal=True):
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
        details: Row = {}
        if row["actor"] is not None and obj(row["actor"]).get("type") is not None:
            details["actor_type"] = string(obj(row["actor"])["type"])
        records.append(
            GitHubRecord(
                str(identifier), "pull_label", fields, details=details, fact=str(identifier)
            )
        )
    records.sort(key=lambda record: record.fields["id"])
    if len({record.key for record in records}) != len(records) or len(set(active)) != len(active):
        raise ValueError("GitHub returned duplicate label evidence")
    actors: dict[str, str | None] = {}
    for record in records:
        name = record.fields["name"]
        if record.fields["action"] == "labeled":
            actors[name] = record.fields.get("actor")
            provenance[name] = {
                "event_id": record.fields["id"],
                "login": record.fields.get("actor"),
                "actor_type": record.details.get("actor_type"),
            }
        else:
            actors.pop(name, None)
            provenance.pop(name, None)
    if any(label in provenance for label in active):
        latest = [
            string(row["name"])
            async for row in api.pages(client, token, f"/issues/{pr.number}/labels")
        ]
        if sorted(active) != sorted(latest):
            raise ValueError("PR labels changed during label poll")
        current = obj(await api.get(client, token, f"/pulls/{pr.number}"))
        if string(obj(current["head"])["sha"]) != pr.head_sha or datetime.fromisoformat(
            string(current["updated_at"])
        ) != datetime.fromisoformat(pr.updated_at):
            raise ValueError("PR changed during label poll")
    return GitHubResource(
        tuple(records),
        attributes={
            "labels": {label: actors.get(label) for label in active},
            "label_provenance": {
                label: provenance[label] for label in active if label in provenance
            },
            "head_sha": pr.head_sha,
            "pull_version": pr.version,
            "pull_updated_at": pr.updated_at,
        },
    )
