"""Read bounded legacy Commit Status evidence for one exact repository head."""

import httpx

from plotroom.adapters.github.read import GitHubReadClient, number, obj, string
from plotroom.core.github import valid_git_sha
from plotroom.core.github_projection import COMMIT_STATUS_BUCKETS, GitHubRecord, GitHubResource

MAX_STATUS_PAGES = 3


async def fetch_commit_statuses(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    sha: str,
) -> GitHubResource:
    if not valid_git_sha(sha):
        raise ValueError("expected the full GitHub status head SHA")
    endpoint = httpx.URL(f"{api.endpoint}/statuses/{sha}")
    records: list[GitHubRecord] = []
    seen: set[int] = set()
    async for row in api.pages(
        client,
        token,
        f"/commits/{sha}/statuses",
        verify_terminal=True,
        verify_boundaries=True,
        max_pages=MAX_STATUS_PAGES,
        require_full_pages=True,
    ):
        identifier = number(row["id"])
        context = string(row["context"])
        state = string(row["state"])
        creator = number(obj(row["creator"])["id"])
        try:
            url = httpx.URL(string(row["url"]))
        except httpx.InvalidURL:
            raise ValueError("invalid GitHub status URL") from None
        if (
            identifier in seen
            or not context.strip()
            or state not in COMMIT_STATUS_BUCKETS
            or url.scheme != endpoint.scheme
            or url.netloc != endpoint.netloc
            or url.userinfo
            or url.query
            or url.fragment
            or url.raw_path.lower() != endpoint.raw_path.lower()
            or not url.path.endswith(f"/{sha}")
        ):
            raise ValueError("invalid or wrongly pinned GitHub commit status")
        seen.add(identifier)
        records.append(
            GitHubRecord(
                str(identifier),
                "commit_status",
                {"id": identifier, "context": context, "state": state, "creator_id": creator},
            )
        )
    return GitHubResource(
        tuple(records), attributes={"repository": api.repository, "head_sha": sha}
    )
