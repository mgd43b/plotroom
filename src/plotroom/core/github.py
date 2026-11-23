"""Typed GitHub facts and their project pull request projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from plotroom.core.events import FieldSpec, NewEvent
from plotroom.core.lanes import infer_lane

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

PULL_REQUESTS = "pull_requests"
PR_OPENED = "github.pr_opened"
PR_MERGED = "github.pr_merged"
PR_FIELDS = (
    FieldSpec("repository", "str"),
    FieldSpec("number", "int"),
    FieldSpec("branch", "str"),
    FieldSpec("head_sha", "str"),
    FieldSpec("state", "str"),
    FieldSpec("updated_at", "str"),
)


@dataclass(frozen=True)
class ObservedPull:
    number: int
    branch: str
    head_sha: str
    state: Literal["open", "closed", "merged"]
    updated_at: str
    body: str


@dataclass(frozen=True)
class PullRequest:
    repository: str
    number: int
    branch: str
    head_sha: str
    state: str
    updated_at: str


@dataclass(frozen=True)
class FetchCursor:
    watermark: str
    as_of: str
    generation: int


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
    for observed in pulls:
        await _project_pull(uow, repository, observed)
    watermark = max((pull.updated_at for pull in pulls), default="")
    if baseline is not None:
        watermark = max(baseline.watermark, watermark)
    await uow.save_fetch_cursor(
        repository,
        PULL_REQUESTS,
        FetchCursor(watermark, as_of, 1 if baseline is None else baseline.generation + 1),
    )
    return True


async def _project_pull(uow: UnitOfWork, repository: str, observed: ObservedPull) -> None:
    current = await uow.pull_request(repository, observed.number)
    if current is not None and current.updated_at > observed.updated_at:
        return
    kind: str | None = None
    if observed.state == "open" and current is None:
        kind = PR_OPENED
    elif observed.state == "merged" and (current is None or current.state != "merged"):
        kind = PR_MERGED
    if kind is None:
        return
    projected = PullRequest(
        repository,
        observed.number,
        observed.branch,
        observed.head_sha,
        observed.state,
        observed.updated_at,
    )
    lane_id = await infer_lane(uow, branch=projected.branch)
    fields = {
        "repository": repository,
        "number": projected.number,
        "branch": projected.branch,
        "head_sha": projected.head_sha,
        "state": projected.state,
        "updated_at": projected.updated_at,
    }
    uid = f"{repository}#{projected.number}@{projected.updated_at}/{projected.head_sha}"
    result = await uow.append(NewEvent("github", kind, uid, fields, observed.body, lane_id))
    if result.created:
        await uow.save_pull_request(projected)
