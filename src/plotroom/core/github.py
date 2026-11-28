"""Typed GitHub facts and their project pull request projection."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from plotroom.core.events import FieldSpec, NewEvent
from plotroom.core.github_projection import (
    ASK_COMMENT_HISTORY,
    COMMENTS,
    FILES,
    ISSUES,
    GitHubRecord,
    GitHubResource,
    apply_resource,
    ask_history_version,
    projected_issues,
)
from plotroom.core.lanes import infer_lane, refresh_ready_lanes

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

PULL_REQUESTS = "pull_requests"
SNAPSHOT_HEALTH = "snapshot_health"
DETAILS = "details"
PULL_DETAIL_ATTEMPT_PREFIX = "pull_details/"
ACTIVE_PULL_DETAIL_BUDGET = 17
TERMINAL_PULL_DETAIL_BUDGET = 2
MERGE_SHA_ATTEMPT_PREFIX = "pull_merge_sha/"
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
    draft: bool | None = False
    merge_commit_sha: str | None = None


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
    draft: bool | None = False
    merge_commit_sha: str | None = None


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


def valid_git_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


async def apply_merge_sha_backfill(
    uow: UnitOfWork, target: PullRequest, merge_sha: str | None, as_of: str
) -> None:
    resource = f"{MERGE_SHA_ATTEMPT_PREFIX}{target.number}"
    prior = await uow.fetch_cursor(target.repository, resource)
    await uow.save_fetch_cursor(
        target.repository,
        resource,
        FetchCursor(
            "", max(as_of, prior.as_of) if prior else as_of, prior.generation + 1 if prior else 1
        ),
    )
    if merge_sha is None or not valid_git_sha(merge_sha):
        return
    current = await uow.pull_request(target.repository, target.number)
    if (
        current is None
        or not current.complete
        or current.state != "merged"
        or current.merge_commit_sha is not None
        or current.head_sha != target.head_sha
    ):
        return
    await uow.save_pull_request(replace(current, merge_commit_sha=merge_sha), merge_sha_only=True)


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
        await _project_pull(uow, repository, observed, as_of, branches, initial=baseline is None)
    for branch in sorted(branches):
        await refresh_ready_lanes(uow, repository, branch)
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
    uow: UnitOfWork,
    repository: str,
    observed: ObservedPull,
    as_of: str,
    branches: set[str],
    *,
    initial: bool,
) -> None:
    current = await uow.pull_request(repository, observed.number)
    if current is not None and (
        current.updated_at > observed.updated_at
        or (current.state == "merged" and observed.state != "merged")
    ):
        return
    await uow.index_pull_body(repository, observed.number, observed.body)
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
        observed.draft,
        current.merge_commit_sha
        if current is not None and current.merge_commit_sha is not None
        else observed.merge_commit_sha,
    )
    if current is not None and current.head_sha != projected.head_sha:
        files = await uow.github_resource(repository, FILES, str(projected.number))
        if files is not None and files.attributes.get("head_sha") is not None:
            await apply_resource(
                uow,
                repository,
                FILES,
                str(projected.number),
                GitHubResource((), complete=False, attributes={"head_sha": None}),
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
    await uow.save_pull_request(projected, historical=initial and current is None)
    uow.pull_overlaps_changed()


async def apply_comments(
    uow: UnitOfWork,
    repository: str,
    baseline: FetchCursor | None,
    comments: tuple[GitHubRecord, ...],
    watermark: str,
    as_of: str,
    *,
    cursor_resource: str = COMMENTS,
) -> None:
    if await uow.fetch_cursor(repository, cursor_resource) != baseline:
        return
    await _apply_comment_records(uow, repository, comments, as_of)
    await uow.save_fetch_cursor(
        repository,
        cursor_resource,
        FetchCursor(watermark, as_of, baseline.generation + 1 if baseline else 1),
    )


async def _apply_comment_records(
    uow: UnitOfWork, repository: str, comments: tuple[GitHubRecord, ...], as_of: str
) -> None:
    for comment in comments:
        prior = await uow.github_resource(repository, COMMENTS, comment.key)
        if prior and prior.records[0].fields["updated_at"] > comment.fields["updated_at"]:
            continue
        if prior is None or prior.records != (comment,):
            await apply_resource(
                uow, repository, COMMENTS, comment.key, GitHubResource((comment,)), as_of
            )


async def apply_ask_comment_history(
    uow: UnitOfWork,
    repository: str,
    number: int,
    version: str,
    comments: tuple[GitHubRecord, ...],
    as_of: str,
) -> None:
    issue = projected_issues(await uow.github_resource(repository, ISSUES)).get(number)
    if issue is None or ask_history_version(issue) != (version, len(comments)):
        return
    await _apply_comment_records(uow, repository, comments, as_of)
    await uow.save_github_resource(
        repository,
        ASK_COMMENT_HISTORY,
        str(number),
        GitHubResource(
            (),
            attributes={
                "version": version,
                "comment_ids": [row.fields["id"] for row in comments],
            },
            as_of=as_of,
        ),
    )
