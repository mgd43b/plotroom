"""Read repository issues, comments, milestones and BMad relationships."""

from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx

from plotroom.adapters.github.read import GitHubReadClient, Row, number, obj, rows, string
from plotroom.core.events import format_time
from plotroom.core.github_projection import GitHubRecord, GitHubResource

MAX_RELATION_REFRESHES = 24


def _state(row: Row) -> str:
    state = string(row["state"])
    if state not in ("open", "closed"):
        raise ValueError("invalid issue state")
    return state


def _body(row: Row) -> str:
    return "" if row["body"] is None else string(row["body"])


def _issue(row: Row) -> tuple[GitHubRecord, ...]:
    n = number(row["number"])
    state = _state(row)
    assignees = sorted({string(user["login"]) for user in rows(row["assignees"])})
    milestone = number(obj(row["milestone"])["number"]) if row["milestone"] is not None else None
    reason = row.get("state_reason")
    issue = {
        "resource": "issue",
        "number": n,
        "state": state,
        "state_reason": string(reason) if reason is not None else None,
        "title": string(row["title"]),
        "milestone": milestone,
        "labels": sorted({string(label["name"]) for label in rows(row["labels"])}),
        "updated_at": string(row["updated_at"]),
    }
    records = [
        GitHubRecord(
            f"issue/{n}",
            f"issue_{'opened' if state == 'open' else 'closed'}",
            {"number": n, "state": state},
            _body(row),
            details=issue,
            fact=state,
        )
    ]
    if assignees:
        records.append(
            GitHubRecord(f"assignees/{n}", "issue_assignees", {"number": n, "assignees": assignees})
        )
    return tuple(records)


async def fetch_issues(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    previous: GitHubResource | None,
) -> GitHubResource:
    records: list[GitHubRecord] = []
    assigned: set[str] = (
        {row.key for row in previous.records if row.kind == "issue_assignees"}
        if previous
        else set()
    )
    async for row in api.pages(client, token, "/issues", params={"state": "all"}):
        if "pull_request" in row:
            continue
        issue = _issue(row)
        records.extend(issue)
        n = issue[0].fields["number"]
        key = f"assignees/{n}"
        if len(issue) == 1 and key in assigned:
            records.append(GitHubRecord(key, "issue_assignees", {"number": n, "assignees": []}))
    return GitHubResource(tuple(records))


def _issue_number(url: str) -> int:
    return number(int(urlsplit(url).path.rsplit("/", 1)[1]))


async def fetch_comments(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    watermark: str,
    issues: frozenset[int],
) -> tuple[tuple[GitHubRecord, ...], str]:
    records: dict[str, GitHubRecord] = {}
    latest = watermark
    params = {"sort": "updated", "direction": "desc"}
    if watermark:
        # GitHub's since filter is exclusive and updated_at has second precision.
        params["since"] = format_time(datetime.fromisoformat(watermark) - timedelta(minutes=5))
    async for row in api.pages(client, token, "/issues/comments", params=params):
        n = _issue_number(string(row["issue_url"]))
        identifier = number(row["id"])
        updated = format_time(datetime.fromisoformat(string(row["updated_at"])))
        latest = max(latest, updated)
        if n not in issues:
            continue
        fields = {
            "id": identifier,
            "number": n,
            "created_at": string(row["created_at"]),
            "updated_at": updated,
        }
        if row["user"] is not None:
            fields["author"] = string(obj(row["user"])["login"])
        key = str(identifier)
        if key not in records or records[key].fields["updated_at"] <= updated:
            records[key] = GitHubRecord(key, "issue_comment", fields, _body(row))
    return tuple(records.values()), latest


def _related(row: Row, repository: str) -> int | str:
    n = number(row["number"])
    owner_repo = urlsplit(string(row["repository_url"])).path.lower().split("/")[-2:]
    qualified = "/".join(owner_repo)
    return n if qualified == repository else f"{qualified}#{n}"


async def _relations(
    api: GitHubReadClient, client: httpx.AsyncClient, token: str, issue: Row
) -> GitHubRecord:
    n = issue["number"]
    related: dict[str, list[int | str]] = {}
    for key, endpoint in (("sub_issues", "sub_issues"), ("blocked_by", "dependencies/blocked_by")):
        references = [
            _related(row, api.repository)
            async for row in api.pages(client, token, f"/issues/{n}/{endpoint}")
        ]
        if key == "sub_issues":
            references = [reference for reference in references if isinstance(reference, int)]
        related[key] = list(dict.fromkeys(references))
    return _issue_metadata(issue, related)


def _issue_metadata(issue: Row, related: dict[str, list[int | str]]) -> GitHubRecord:
    n = issue["number"]
    fields = {"number": n, "labels": issue["labels"], **related}
    if issue["milestone"] is not None:
        fields["milestone"] = issue["milestone"]
    return GitHubRecord(
        f"metadata/{n}",
        "issue_metadata",
        fields,
        details={**issue, **related},
    )


async def fetch_issue_tree(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    issues: GitHubResource,
    previous: GitHubResource | None,
) -> GitHubResource:
    old = {row.key: row for row in previous.records} if previous is not None else {}
    records: list[GitHubRecord] = []
    async for row in api.pages(client, token, "/milestones", params={"state": "all"}):
        n = number(row["number"])
        fields = {"number": n, "state": _state(row), "title": string(row["title"])}
        records.append(
            GitHubRecord(
                f"milestone/{n}",
                "milestone",
                fields,
                details={
                    **fields,
                    "resource": "milestone",
                },
            )
        )
    pending: list[Row] = []
    reconcile: list[Row] = []
    for record in issues.records:
        if record.details.get("resource") != "issue":
            continue
        issue = {**record.details, "body": record.body}
        cached = old.get(f"metadata/{issue['number']}")
        if cached is None or cached.details["updated_at"] != issue["updated_at"]:
            pending.append(issue)
        else:
            records.append(
                _issue_metadata(
                    issue,
                    {
                        "sub_issues": cached.details["sub_issues"],
                        "blocked_by": cached.details["blocked_by"],
                    },
                )
            )
            # Relation changes need not change issue timestamps. Reconcile these
            # separately from known issue changes and bound requests per sweep.
            if previous and previous.generation % 8 == issue["number"] % 8:
                reconcile.append(issue)
    if previous and len(reconcile) > MAX_RELATION_REFRESHES:
        offset = (previous.generation // 8 * MAX_RELATION_REFRESHES) % len(reconcile)
        reconcile = reconcile[offset:] + reconcile[:offset]
    refresh = (pending + reconcile)[:MAX_RELATION_REFRESHES]
    for issue in refresh:
        records.append(await _relations(api, client, token, issue))
    complete = len(pending) <= MAX_RELATION_REFRESHES
    return GitHubResource(tuple(records), complete=complete)
