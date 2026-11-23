"""Read net file changes for a pinned pull head and default branch tip."""

from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from plotroom.adapters.github.ci import comparison_paths
from plotroom.adapters.github.read import GitHubReadClient, Row, obj, rows, string
from plotroom.core.events import format_time
from plotroom.core.github import PullRequest
from plotroom.core.github_projection import GitHubResource
from plotroom.core.merge_overlap import full_sha


async def _compare(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, base: str, head: str
) -> Row:
    comparison = obj(
        await api.get(client, token, f"/compare/{base}...{head}", {"page": 1, "per_page": 1})
    )
    if obj(comparison["base_commit"])["sha"] != base:
        raise ValueError("GitHub returned a different comparison base")
    return comparison


def _net_paths(comparison: Row, base: str) -> list[str]:
    files: Any = comparison["files"]
    if (
        comparison["status"] != "ahead"
        or obj(comparison["merge_base_commit"])["sha"] != base
        or len(rows(files)) >= 300
    ):
        raise ValueError("incomplete comparison files")
    return sorted(comparison_paths(files))


async def fetch_pull_comparison(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    pr: PullRequest,
    main_branch: str,
    main_tip: str,
    previous: GitHubResource | None = None,
) -> GitHubResource:
    if (
        not full_sha(pr.head_sha)
        or not full_sha(main_tip)
        or pr.head_repository is None
        or pr.state != "open"
    ):
        return GitHubResource((), complete=False)
    pinned = previous.attributes if previous is not None and previous.complete else {}
    if pinned.get("head_sha") != pr.head_sha or pinned.get("main_tip") != main_tip:
        pinned = await _file_pair(api, client, token, pr.head_sha, main_tip)
    # A comparison is immutable, but the refs that make it relevant can move
    # while GitHub serves it. Confirm both and the PR's current base target.
    current = obj(await api.get(client, token, f"/pulls/{pr.number}"))
    head, base = obj(current["head"]), obj(current["base"])
    branch = obj(await api.get(client, token, f"/branches/{quote(main_branch, safe='')}"))
    if (
        current["state"] != "open"
        or head["sha"] != pr.head_sha
        or head["ref"] != pr.branch
        or obj(head["repo"])["full_name"] != pr.head_repository
        or format_time(datetime.fromisoformat(string(current["updated_at"]))) != pr.updated_at
        or base["ref"] != main_branch
        or obj(base["repo"])["full_name"] != api.repository
        or obj(branch["commit"])["sha"] != main_tip
    ):
        return GitHubResource((), complete=False)
    return GitHubResource(
        (),
        attributes={
            "merge_base": pinned["merge_base"],
            "pull_paths": pinned["pull_paths"],
            "main_paths": pinned["main_paths"],
            "pull_version": pr.version,
            "pull_updated_at": pr.updated_at,
            "head_sha": pr.head_sha,
            "main_tip": main_tip,
            "branch": main_branch,
        },
    )


async def _file_pair(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, pr_head: str, main_tip: str
) -> Row:
    if pr_head == main_tip:
        return {"merge_base": main_tip, "pull_paths": [], "main_paths": []}
    compared = await _compare(api, client, token, pr_head, main_tip)
    merge_base = string(obj(compared["merge_base_commit"])["sha"])
    if not full_sha(merge_base):
        raise ValueError("GitHub returned an invalid merge base")

    async def paths(head: str) -> list[str]:
        if head == merge_base:
            return []
        return _net_paths(await _compare(api, client, token, merge_base, head), merge_base)

    return {
        "merge_base": merge_base,
        "pull_paths": await paths(pr_head),
        "main_paths": await paths(main_tip),
    }
