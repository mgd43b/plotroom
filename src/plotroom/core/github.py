"""Typed GitHub facts and their project pull request projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from plotroom.core.events import FieldSpec, NewEvent
from plotroom.core.github_projection import (
    COMMENTS,
    FILES,
    GitHubRecord,
    GitHubResource,
    apply_resource,
)
from plotroom.core.lanes import infer_lane, refresh_ready_lane

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

PULL_REQUESTS = "pull_requests"
DETAILS = "details"
PR_OPENED = "github.pr_opened"
PR_MERGED = "github.pr_merged"
PR_CLOSED = "github.pr_closed"
PR_REOPENED = "github.pr_reopened"
PR_HEAD_CHANGED = "github.pr_head_changed"
PR_KINDS = (PR_OPENED, PR_MERGED, PR_CLOSED, PR_REOPENED, PR_HEAD_CHANGED)
PR_FIELDS = (
    FieldSpec("repository", "str"),
    FieldSpec("number", "int"),
    FieldSpec("branch", "str"),
    FieldSpec("head_sha", "str"),
    FieldSpec("head_repository", "str", required=False),
    FieldSpec("state", "str"),
    FieldSpec("updated_at", "str"),
)


@dataclass(frozen=True)
class ObservedPull:
    number: int
    branch: str
    head_sha: str
    head_repository: str | None
    state: Literal["open", "closed", "merged"]
    updated_at: str
    body: str


@dataclass(frozen=True)
class PullRequest:
    repository: str
    number: int
    branch: str
    head_sha: str
    head_repository: str | None
    state: str
    updated_at: str
    as_of: str
    complete: bool
    version: int


@dataclass(frozen=True)
class FetchCursor:
    watermark: str
    as_of: str
    generation: int


@dataclass(frozen=True)
class FetchRetry:
    until: str
    next_backoff: int


@dataclass(frozen=True)
class GitHubSummary:
    open_prs: int
    as_of: str | None


async def apply_pull_poll(
    uow: UnitOfWork,
    repository: str,
    baseline: FetchCursor | None,
    pulls: tuple[ObservedPull, ...],
    as_of: str,
) -> bool:
    if await uow.fetch_cursor(repository, PULL_REQUESTS) != baseline:
        return False
    branches: set[str] = set()
    for observed in pulls:
        await _project_pull(uow, repository, observed, as_of, branches)
    for branch in sorted(branches):
        await refresh_ready_lane(uow, repository, branch)
    watermark = max((pull.updated_at for pull in pulls), default="")
    if baseline is not None:
        watermark = max(baseline.watermark, watermark)
    await uow.save_fetch_cursor(
        repository,
        PULL_REQUESTS,
        FetchCursor(watermark, as_of, 1 if baseline is None else baseline.generation + 1),
    )
    await uow.save_fetch_retry(repository, PULL_REQUESTS, None)
    return True


def _changes(current: PullRequest | None, observed: ObservedPull) -> tuple[str, ...]:
    by_state = {
        "open": PR_OPENED if current is None else PR_REOPENED,
        "closed": PR_CLOSED,
        "merged": PR_MERGED,
    }
    if current is None:
        return (by_state[observed.state],)
    changes: list[str] = []
    if (current.branch, current.head_sha, current.head_repository) != (
        observed.branch,
        observed.head_sha,
        observed.head_repository,
    ):
        changes.append(PR_HEAD_CHANGED)
    if current.state != observed.state:
        changes.append(by_state[observed.state])
    return tuple(changes)


async def _project_pull(
    uow: UnitOfWork, repository: str, observed: ObservedPull, as_of: str, branches: set[str]
) -> None:
    current = await uow.pull_request(repository, observed.number)
    if current is not None and (
        current.updated_at > observed.updated_at
        or (current.state == "merged" and observed.state != "merged")
    ):
        return
    changes = _changes(current, observed)
    previous_version = current.version if current is not None else 0
    projected = PullRequest(
        repository,
        observed.number,
        observed.branch,
        observed.head_sha,
        observed.head_repository,
        observed.state,
        observed.updated_at,
        as_of,
        True,
        previous_version + len(changes),
    )
    if current is not None and current.head_sha != projected.head_sha:
        files = await uow.github_resource(repository, FILES, str(projected.number))
        if files is not None and files.complete:
            await apply_resource(
                uow,
                repository,
                FILES,
                str(projected.number),
                GitHubResource((), complete=False),
                as_of,
            )
    if not changes:
        if current != projected:
            await uow.save_pull_request(projected)
        return
    if current is not None and current.head_repository == repository:
        branches.add(current.branch)
    if projected.head_repository == repository:
        branches.add(projected.branch)
    lane_id = (
        await infer_lane(uow, branch=projected.branch)
        if projected.head_repository == repository
        else None
    )
    fields = {
        "repository": repository,
        "number": projected.number,
        "branch": projected.branch,
        "head_sha": projected.head_sha,
        "state": projected.state,
        "updated_at": projected.updated_at,
    }
    if projected.head_repository is not None:
        fields["head_repository"] = projected.head_repository
    for version, kind in enumerate(changes, start=previous_version + 1):
        uid = f"{repository}#{projected.number}@{version}:{projected.updated_at}"
        await uow.append(NewEvent("github", kind, uid, fields, observed.body, lane_id))
    await uow.save_pull_request(projected)


async def apply_comments(
    uow: UnitOfWork,
    repository: str,
    baseline: FetchCursor | None,
    comments: tuple[GitHubRecord, ...],
    watermark: str,
    as_of: str,
) -> None:
    if await uow.fetch_cursor(repository, COMMENTS) != baseline:
        return
    for comment in comments:
        prior = await uow.github_resource(repository, COMMENTS, comment.key)
        if prior and prior.records[0].fields["updated_at"] > comment.fields["updated_at"]:
            continue
        if prior is None or prior.records != (comment,):
            await apply_resource(
                uow, repository, COMMENTS, comment.key, GitHubResource((comment,)), as_of
            )
    await uow.save_fetch_cursor(
        repository,
        COMMENTS,
        FetchCursor(watermark, as_of, baseline.generation + 1 if baseline else 1),
    )
