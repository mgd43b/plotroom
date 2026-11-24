"""Read repository issues, comments, milestones and BMad relationships."""

from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx

from plotroom.adapters.github import GitHubRateLimited, GitHubUnavailable
from plotroom.adapters.github.read import GitHubReadClient, Row, number, obj, rows, string
from plotroom.core.events import format_time
from plotroom.core.github_projection import (
    ISSUE_PAGINATION_VERIFIED,
    ISSUE_RELATION_RETRY,
    GitHubRecord,
    GitHubResource,
)

MAX_RELATION_REFRESHES = 24


class IssueRelationshipReadError(GitHubUnavailable):
    """A failed relationship batch whose discarded results need to be refreshed."""

    def __init__(self, cause: Exception, numbers: tuple[int, ...]) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.numbers = numbers


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
    count = row.get("comments")
    if type(count) is int and count >= 0:
        issue["comment_count"] = count
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
    seen: set[int] = set()
    async for row in api.pages(
        client,
        token,
        "/issues",
        params={"state": "all"},
        verify_terminal=True,
        verify_boundaries=True,
    ):
        n = number(row["number"])
        if n in seen:
            raise GitHubUnavailable("GitHub repeated an issue number across pages")
        seen.add(n)
        if "pull_request" in row:
            continue
        issue = _issue(row)
        records.extend(issue)
        key = f"assignees/{n}"
        if len(issue) == 1 and key in assigned:
            records.append(GitHubRecord(key, "issue_assignees", {"number": n, "assignees": []}))
    return GitHubResource(tuple(records), attributes={ISSUE_PAGINATION_VERIFIED: True})


def _issue_number(url: str) -> int:
    return number(int(urlsplit(url).path.rsplit("/", 1)[1]))


async def fetch_comments(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    watermark: str,
    issues: frozenset[int],
) -> tuple[tuple[GitHubRecord, ...], str]:
    observed: list[Row] = []
    params = {"sort": "updated", "direction": "desc"}
    if watermark:
        # GitHub's since filter is exclusive and updated_at has second precision.
        params["since"] = format_time(datetime.fromisoformat(watermark) - timedelta(minutes=5))
    async for row in api.pages(
        client, token, "/issues/comments", params=params, recent=not watermark
    ):
        observed.append(row)
    return _comments(observed, issues, watermark)


def _comments(
    observed: list[Row], issues: frozenset[int], latest: str = ""
) -> tuple[tuple[GitHubRecord, ...], str]:
    records: dict[str, GitHubRecord] = {}
    for row in observed:
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


async def fetch_issue_comments(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    issue: int,
    version: str,
    expected_count: int,
) -> tuple[GitHubRecord, ...]:
    observed = [
        row
        async for row in api.pages(client, token, f"/issues/{issue}/comments", verify_terminal=True)
    ]
    if len(observed) != expected_count:
        raise GitHubUnavailable("GitHub issue comment count changed during history fetch")
    issue_path = f"/repos/{api.repository}/issues/{issue}".casefold()
    if any(
        not urlsplit(string(row["issue_url"])).path.casefold().endswith(issue_path)
        for row in observed
    ):
        raise GitHubUnavailable("GitHub returned a comment for a different issue")
    comments, _ = _comments(observed, frozenset({issue}))
    if len(comments) != len(observed):
        raise GitHubUnavailable("GitHub returned duplicate comments in issue history")
    # A replacement during offset pagination can preserve the count while shifting a page.
    current = obj(await api.get(client, token, f"/issues/{issue}"))
    count = current.get("comments")
    if (
        "pull_request" in current
        or number(current["number"]) != issue
        or _state(current) != "closed"
        or type(count) is not int
        or f"{string(current['updated_at'])}/{count}" != version
    ):
        raise GitHubUnavailable("GitHub issue changed during comment history verification")
    return comments


async def fetch_comment_backfill(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    position: str,
    issues: frozenset[int],
) -> tuple[tuple[GitHubRecord, ...], str]:
    # Overlap a page to tolerate older comments being deleted during the backfill.
    page = int(position or "1")
    records: list[Row] = []
    for offset in range(max(1, page - 1), page + 4):
        batch, more = await api.page(
            client,
            token,
            "/issues/comments",
            offset,
            params={"sort": "created", "direction": "asc"},
        )
        records.extend(batch)
        if not more:
            return _comments(records, issues)[0], "done"
    return _comments(records, issues)[0], str(page + 4)


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
            async for row in api.pages(
                client,
                token,
                f"/issues/{n}/{endpoint}",
                verify_terminal=True,
                verify_boundaries=True,
            )
        ]
        if len(set(references)) != len(references):
            raise GitHubUnavailable("GitHub repeated an issue relationship across pages")
        related[key] = [
            reference
            for reference in references
            if key != "sub_issues" or isinstance(reference, int)
        ]
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
        details={**issue, **related, ISSUE_PAGINATION_VERIFIED: True},
    )


async def fetch_issue_tree(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    issues: GitHubResource,
    previous: GitHubResource | None,
) -> GitHubResource:
    retries: set[int] = (
        set(previous.attributes.get(ISSUE_RELATION_RETRY, [])) if previous else set()
    )
    old = (
        {
            row.key: row
            for row in previous.records
            if row.details.get(ISSUE_PAGINATION_VERIFIED) is True
            and row.fields["number"] not in retries
        }
        if previous
        else {}
    )
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
    reconcile_count = min(len(reconcile), max(1, MAX_RELATION_REFRESHES // 4))
    if previous and len(reconcile) > reconcile_count:
        offset = (previous.generation // 8 * reconcile_count) % len(reconcile)
        reconcile = reconcile[offset:] + reconcile[:offset]
    pending_count = MAX_RELATION_REFRESHES - reconcile_count
    fresh = [issue for issue in pending if issue["number"] not in retries]
    retry_pending = [issue for issue in pending if issue["number"] in retries]
    if previous is not None and retry_pending:
        offset = previous.generation % len(retry_pending)
        retry_pending = retry_pending[offset:] + retry_pending[:offset]
    refresh = (fresh + retry_pending)[:pending_count] + reconcile[:reconcile_count]
    refreshed: set[int] = set()
    failed: set[int] = set()
    for issue in refresh:
        issue_id: int = issue["number"]
        try:
            records.append(await _relations(api, client, token, issue))
        except GitHubRateLimited as exc:
            raise IssueRelationshipReadError(
                exc, tuple(candidate["number"] for candidate in refresh)
            ) from exc
        except (
            GitHubUnavailable,
            httpx.RequestError,
            KeyError,
            ValueError,
            TypeError,
            OverflowError,
        ):
            failed.add(issue_id)
        else:
            refreshed.add(issue_id)
    return GitHubResource(
        tuple(
            row
            for row in records
            if row.fields["number"] not in failed or row.kind != "issue_metadata"
        ),
        complete=len(pending) <= pending_count and not failed,
        attributes={
            ISSUE_RELATION_RETRY: sorted(retries.difference(refreshed) | failed),
            ISSUE_PAGINATION_VERIFIED: True,
        },
    )
