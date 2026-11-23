"""Bounded deployment-status polling with durable progress through history."""

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

import httpx

from plotroom.adapters.github import GitHubRateLimited, GitHubUnavailable
from plotroom.adapters.github.read import GitHubReadClient, Row, number, string
from plotroom.core.events import NewEvent
from plotroom.core.github import FetchCursor
from plotroom.core.lanes import infer_lane
from plotroom.core.reports import DEPLOY_STATUS, deploy_fields

if TYPE_CHECKING:
    from plotroom.core.ledger import Ledger
    from plotroom.core.unit_of_work import UnitOfWork

log = logging.getLogger("plotroom.github")
DEPLOYMENTS = "deployments"
_HEAD_DEPLOYMENTS = 2
_SWEEP_BATCH = 8
_READ_ERRORS = (
    GitHubUnavailable,
    httpx.RequestError,
    KeyError,
    ValueError,
    TypeError,
    OverflowError,
)


async def poll_deployments(
    ledger: Ledger, api: GitHubReadClient, client: httpx.AsyncClient, token: str, as_of: str
) -> None:
    """Refresh the newest deployments and visit a bounded slice of older ones per poll."""
    repository = api.repository
    cursor = await ledger.fetch_cursor(repository, DEPLOYMENTS)
    page, offset = (1, 0) if cursor is None else map(int, cursor.watermark.split(":"))
    extra = {"project": ledger.project_id}
    try:
        newest, has_older = await api.page(client, token, "/deployments", 1)
    except GitHubRateLimited:
        raise
    except _READ_ERRORS:
        log.warning("GitHub deployment refresh failed", extra=extra)
        return

    if not has_older and page > 1:
        page, offset = 1, 0
    current, more = newest, has_older
    if page > 1:
        try:
            current, more = await api.page(client, token, "/deployments", page)
        except GitHubRateLimited:
            raise
        except _READ_ERRORS:
            log.warning("GitHub deployment history refresh failed", extra=extra)
            # A page can disappear when deployments are deleted between polls.
            current, more = [], False

    end = offset + _SWEEP_BATCH
    scanned: set[int] = set()
    for row in [*newest[:_HEAD_DEPLOYMENTS], *current[offset:end]]:
        try:
            deployment_id = number(row["id"])
            if deployment_id not in scanned:
                scanned.add(deployment_id)
                await _poll_statuses(ledger, api, client, token, row, deployment_id, as_of)
        except GitHubRateLimited:
            raise
        except _READ_ERRORS:
            log.warning("GitHub deployment status refresh failed", extra=extra)

    next_page = page + 1 if more else 1
    watermark = f"{page}:{end}" if end < len(current) else f"{next_page}:0"
    if watermark != (cursor.watermark if cursor else "1:0"):
        progress = FetchCursor(watermark, as_of, cursor.generation + 1 if cursor else 1)
        await ledger.run(
            "system", lambda uow: uow.save_fetch_cursor(repository, DEPLOYMENTS, progress)
        )


async def _poll_statuses(
    ledger: Ledger,
    api: GitHubReadClient,
    client: httpx.AsyncClient,
    token: str,
    deployment: Row,
    identifier: int,
    as_of: str,
) -> None:
    resource = f"deployment_statuses/{identifier}"
    path = f"/deployments/{identifier}/statuses"
    recent, has_history = await api.page(client, token, path, 1)
    cursor = await ledger.fetch_cursor(api.repository, resource)
    page = int(cursor.watermark) if cursor else 2
    history, more = [], False
    if has_history:
        try:
            history, more = await api.page(client, token, path, page)
        except GitHubRateLimited:
            raise
        except _READ_ERRORS:
            log.warning(
                "GitHub deployment status history refresh failed",
                extra={"project": ledger.project_id},
            )
    watermark = str(page + 1 if more else 2)
    if not recent and not history and cursor is None:
        return

    sha = string(deployment["sha"])
    ref = string(deployment.get("ref") or "")
    branch = ref.removeprefix("refs/heads/")
    if branch == sha or branch.startswith("refs/"):
        branch = ""
    events = tuple(
        _status_event(api.repository, deployment, identifier, sha, row)
        for row in [*recent, *history]
    )
    progress = (
        FetchCursor(watermark, as_of, cursor.generation + 1 if cursor else 1)
        if watermark != (cursor.watermark if cursor else "2")
        else None
    )
    await ledger.run(
        "system", lambda uow: _record(uow, api.repository, resource, branch, events, progress)
    )


def _status_event(
    repository: str, deployment: Row, identifier: int, sha: str, status: Row
) -> NewEvent:
    status_id = number(status["id"])
    environment = string(status.get("environment") or deployment["environment"])
    fields = deploy_fields(environment, sha, string(status["state"]))
    return NewEvent(
        "deploy",
        DEPLOY_STATUS,
        f"github:{repository}:{identifier}:{status_id}",
        {**fields, "repository": repository, "deployment_id": identifier, "status_id": status_id},
    )


async def _record(
    uow: UnitOfWork,
    repository: str,
    resource: str,
    branch: str,
    events: tuple[NewEvent, ...],
    progress: FetchCursor | None,
) -> None:
    lane = await infer_lane(uow, branch=branch) if branch else None
    for event in events:
        await uow.append(replace(event, lane_id=lane))
    if progress is not None:
        await uow.save_fetch_cursor(repository, resource, progress)
