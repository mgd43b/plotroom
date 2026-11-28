"""Read exact-head Kodus check evidence for one open pull request."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx

from plotroom.adapters.github import GitHubUnavailable
from plotroom.adapters.github.read import (
    GitHubReadClient,
    ReviewThreadPull,
    Row,
    number,
    obj,
    rows,
    string,
)
from plotroom.core.events import format_time
from plotroom.core.github import PullRequest, valid_git_sha
from plotroom.core.github_projection import GitHubResource
from plotroom.core.kodus_review import KodusReviewPolicy

_REVIEWED = "Review finished successfully. Suggestions (if any) were posted as PR/file comments."
_CADENCE_SKIP = "Manual mode requires @kody start-review."
_MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


@dataclass(frozen=True)
class PullIdentity:
    pull_id: int
    base_repository_id: int
    head_repository_id: int
    created_at: datetime


@dataclass(frozen=True)
class ReviewCheck:
    id: int
    mode: Literal["reviewed", "cadence-skip"]
    fingerprint: str
    head_sha: str
    started_at: datetime
    completed_at: datetime
    association: tuple[int, int, int, int, str] | None


def _nonempty(value: object) -> str:
    result = string(value)
    if not result.strip():
        raise ValueError("GitHub returned an empty identity")
    return result


def _time(value: object) -> datetime:
    timestamp = datetime.fromisoformat(string(value))
    if timestamp.utcoffset() is None:
        raise ValueError("GitHub returned a timestamp without an offset")
    return timestamp


def _pull_identity(row: Row, pull: PullRequest) -> PullIdentity:
    base, head = obj(row["base"]), obj(row["head"])
    base_repo, head_repo = obj(base["repo"]), obj(head["repo"])
    if (
        number(row["number"]) != pull.number
        or row["state"] != "open"
        or string(base_repo["full_name"]).lower() != pull.repository
        or string(head_repo["full_name"]).lower() != pull.head_repository
        or head["ref"] != pull.branch
        or head["sha"] != pull.head_sha
        or _time(row["updated_at"]) != _time(pull.updated_at)
    ):
        raise GitHubUnavailable("GitHub returned a different pull request revision")
    return PullIdentity(
        number(row["id"]),
        number(base_repo["id"]),
        number(head_repo["id"]),
        _time(row["created_at"]),
    )


def _is_kodus_check(row: Row, policy: KodusReviewPolicy) -> bool:
    raw_app = row.get("app")
    if row.get("name") != policy.check_name or not isinstance(raw_app, dict):
        return False
    app = obj(raw_app)
    return (
        type(app.get("id")) is int
        and app["id"] == policy.app_id
        and app.get("slug") == policy.app_slug
    )


def _pull_association(row: Row) -> tuple[int, int, int, int, str]:
    base, head = obj(row["base"]), obj(row["head"])
    return (
        number(row["number"]),
        number(row["id"]),
        number(obj(base["repo"])["id"]),
        number(obj(head["repo"])["id"]),
        string(head["sha"]),
    )


def parse_review_check(row: Row, policy: KodusReviewPolicy) -> ReviewCheck | None:
    """Recognize the App's exact result and fingerprint the check inputs used by the gate."""
    if not _is_kodus_check(row, policy) or row.get("status") != "completed":
        return None
    try:
        signature = (row["conclusion"], obj(row["output"])["summary"])
        if signature == ("success", _REVIEWED):
            mode = "reviewed"
        elif signature == ("skipped", _CADENCE_SKIP):
            mode = "cadence-skip"
        else:
            return None
        run_id, head_sha = number(row["id"]), string(row["head_sha"])
        if not valid_git_sha(head_sha):
            return None
        started_at, completed_at = _time(row["started_at"]), _time(row["completed_at"])
        associations = rows(row["pull_requests"])
        if len(associations) > 1:
            return None
        association = _pull_association(associations[0]) if associations else None
        canonical = (
            mode,
            run_id,
            head_sha,
            policy.app_id,
            policy.app_slug,
            policy.check_name,
            format_time(started_at),
            format_time(completed_at),
            association,
        )
        fingerprint = hashlib.sha256(json.dumps(canonical).encode()).hexdigest()
    except KeyError, TypeError, ValueError, OverflowError:
        return None
    return ReviewCheck(run_id, mode, fingerprint, head_sha, started_at, completed_at, association)


async def _review_form_binds_pull(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    pull: PullRequest,
    policy: KodusReviewPolicy,
    sha: str,
    started_at: datetime,
    completed_at: datetime,
) -> bool:
    """Corroborate an unassociated success with a submitted exact-commit review."""
    path = f"/pulls/{pull.number}"
    bot_login = f"{policy.app_slug}[bot]"
    url_prefix = f"{api.api_url}/repos/"
    identifiers: set[int] = set()
    matches = 0
    async for review in api.pages(
        client,
        token,
        f"{path}/reviews",
        verify_terminal=True,
        verify_boundaries=True,
    ):
        review_id = number(review["id"])
        url = string(review["pull_request_url"])
        if (
            review_id in identifiers
            or not url.startswith(url_prefix)
            or not url.endswith(path)
            or url.removeprefix(url_prefix).removesuffix(path).lower() != api.repository.lower()
        ):
            return False
        identifiers.add(review_id)
        user = obj(review["user"])
        if _nonempty(user["login"]).lower() != bot_login:
            continue
        if user["type"] != "Bot":
            return False
        submitted_at = _time(review["submitted_at"])
        if not started_at <= submitted_at <= completed_at:
            continue
        explicit_app = review.get("performed_via_github_app")
        if explicit_app is not None:
            app = obj(explicit_app)
            if number(app["id"]) != policy.app_id or string(app["slug"]) != policy.app_slug:
                return False
        if review["commit_id"] != sha or review["state"] != "COMMENTED":
            return False
        matches += 1
    return matches == 1


async def _check(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    pull: PullRequest,
    identity: PullIdentity,
    policy: KodusReviewPolicy,
    sha: str,
) -> ReviewCheck:
    if not valid_git_sha(sha):
        raise ValueError("invalid Kodus check head")
    trusted: list[Row] = []
    identifiers: set[int] = set()
    async for row in api.pages(
        client,
        token,
        f"/commits/{sha}/check-runs",
        key="check_runs",
        params={"check_name": policy.check_name, "filter": "latest"},
        verify_total=True,
        verify_boundaries=True,
    ):
        run_id = number(row["id"])
        if run_id in identifiers:
            raise GitHubUnavailable("GitHub returned duplicate check IDs")
        identifiers.add(run_id)
        if _is_kodus_check(row, policy):
            trusted.append(row)
    if len(trusted) != 1:
        raise GitHubUnavailable("GitHub did not return one trusted Kodus check")
    check = parse_review_check(trusted[0], policy)
    if (
        check is None
        or check.head_sha != sha
        or not identity.created_at <= check.started_at <= check.completed_at
    ):
        raise GitHubUnavailable("Kodus check does not prove an exact completed-head outcome")
    if check.association == (
        pull.number,
        identity.pull_id,
        identity.base_repository_id,
        identity.head_repository_id,
        sha,
    ):
        return check
    if (
        check.association is None
        and check.mode == "reviewed"
        and await _review_form_binds_pull(
            api, client, token, pull, policy, sha, check.started_at, check.completed_at
        )
    ):
        return check
    raise GitHubUnavailable("Kodus check lacks a verified review or cadence skip for this pull")


def _answered(thread: Row, pr_author: str) -> bool:
    if thread["isResolved"] is not True:
        return False
    connection = obj(thread["comments"])
    comments = rows(connection["nodes"])
    count = number(connection["totalCount"])
    if not count == len(comments) <= 100 or obj(connection["pageInfo"])["hasNextPage"] is not False:
        return False
    identifiers = {_nonempty(comment["id"]) for comment in comments}
    roots = [comment for comment in comments if comment["replyTo"] is None]
    if len(identifiers) != count or len(roots) != 1:
        return False
    root = roots[0]
    reviewer = _nonempty(obj(root["author"])["login"]).lower()
    answered = False
    for comment in comments:
        actor = obj(comment["author"])
        login = _nonempty(actor["login"]).lower()
        kind = _nonempty(actor["__typename"])
        association = _nonempty(comment["authorAssociation"])
        if comment is root:
            continue
        if obj(comment["replyTo"])["id"] != root["id"]:
            return False
        if (
            kind == "User"
            and login != reviewer
            and (login == pr_author or association in _MAINTAINER_ASSOCIATIONS)
        ):
            answered = True
    return answered


async def fetch_kodus_review(
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    pull: PullRequest,
    policy: KodusReviewPolicy,
) -> GitHubResource:
    if pull.repository != api.repository.lower():
        raise ValueError("Kodus evidence requested from a different repository")
    path = f"/pulls/{number(pull.number)}"
    detail = obj(await api.get(client, token, path))
    identity = _pull_identity(detail, pull)
    check = await _check(api, client, token, pull, identity, policy, pull.head_sha)
    evidence: Row = {
        "repository": pull.repository,
        "pull_number": pull.number,
        "pull_id": identity.pull_id,
        "created_at": format_time(identity.created_at),
        "head_sha": pull.head_sha,
        "pull_version": pull.version,
        "pull_updated_at": pull.updated_at,
        "app_id": policy.app_id,
        "app_slug": policy.app_slug,
        "check_name": policy.check_name,
        "check_id": check.id,
        "check_fingerprint": check.fingerprint,
        "mode": check.mode,
    }
    if check.mode == "cadence-skip":
        opening = policy.opening_head(pull.repository, pull.number)
        if (
            opening is None
            or opening.head_sha == pull.head_sha
            or opening.pull_id != identity.pull_id
            or opening.created_at != evidence["created_at"]
        ):
            raise GitHubUnavailable("No trusted opening head attestation for this pull request")
        opening_check = await _check(api, client, token, pull, identity, policy, opening.head_sha)
        if opening_check.mode != "reviewed" or opening_check.id == check.id:
            raise GitHubUnavailable("Opening head lacks a completed Kodus review")
        author = _nonempty(obj(detail["user"])["login"]).lower()
        thread_pull = ReviewThreadPull(_nonempty(detail["node_id"]), author)
        answered_threads: list[str] = []
        async for thread in api.review_threads(
            client, token, pull.number, pull.head_sha, cadence=thread_pull
        ):
            if not _answered(thread, author):
                raise GitHubUnavailable("A cadence review thread is unresolved or unanswered")
            answered_threads.append(string(thread["id"]))
        evidence.update(
            opening_sha=opening.head_sha,
            opening_check_id=opening_check.id,
            answered_threads=sorted(answered_threads),
        )
    if _pull_identity(obj(await api.get(client, token, path)), pull) != identity:
        raise GitHubUnavailable("Pull identity changed during the Kodus review read")
    return GitHubResource((), attributes=evidence)
