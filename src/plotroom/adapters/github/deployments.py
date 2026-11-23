"""Bounded deployment-status polling with durable progress through history."""

import json
import logging
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

import httpx

from plotroom.adapters.github import GitHubRateLimited, GitHubUnavailable
from plotroom.adapters.github.read import GitHubReadClient, Row, number, rows, string
from plotroom.core.events import NewEvent
from plotroom.core.github import FetchCursor
from plotroom.core.lanes import infer_lane
from plotroom.core.reports import DEPLOY_STATUS, deploy_fields, validate_report_sha

if TYPE_CHECKING:
    from plotroom.core.ledger import Ledger
    from plotroom.core.unit_of_work import UnitOfWork

log = logging.getLogger("plotroom.github")
DEPLOYMENTS = "deployments"
_PENDING_DEPLOYMENTS = "deployment_pending"
_HEAD_DEPLOYMENTS = 2
_PENDING_BATCH = 2
_PENDING_LIMIT = 64
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
    pending_cursor = await ledger.fetch_cursor(repository, _PENDING_DEPLOYMENTS)
    queued = rows(json.loads(pending_cursor.watermark)) if pending_cursor else []
    pending = {number(row["id"]): row for row in queued}
    scanned: set[int] = set()
    candidates = [*newest[:_HEAD_DEPLOYMENTS], *queued[:_PENDING_BATCH], *current[offset:end]]
    try:
        for row in candidates:
            try:
                deployment_id = number(row["id"])
            except _READ_ERRORS:
                log.warning("GitHub deployment row was invalid", extra=extra)
                continue
            if deployment_id in scanned:
                continue
            scanned.add(deployment_id)
            try:
                follow = await _poll_statuses(ledger, api, client, token, row, deployment_id, as_of)
            except GitHubRateLimited:
                raise
            except _READ_ERRORS:
                follow = pending.get(deployment_id)
                log.warning("GitHub deployment status refresh failed", extra=extra)
            pending.pop(deployment_id, None)
            if follow is not None:
                pending[deployment_id] = follow
    finally:
        remaining = list(pending.values())[-_PENDING_LIMIT:]
        if remaining != queued:
            progress = FetchCursor(
                json.dumps(remaining), as_of, pending_cursor.generation + 1 if pending_cursor else 1
            )
            await ledger.run(
                "system",
                lambda uow: uow.save_fetch_cursor(repository, _PENDING_DEPLOYMENTS, progress),
            )

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
) -> Row | None:
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
    sha = validate_report_sha(string(deployment["sha"]))
    ref = string(deployment.get("ref") or "")
    branch = ref.removeprefix("refs/heads/")
    if branch == sha or branch.startswith("refs/"):
        branch = ""

    def valid_events(statuses: list[Row]) -> list[NewEvent]:
        events: list[NewEvent] = []
        for row in statuses:
            try:
                events.append(_status_event(api.repository, deployment, identifier, sha, row))
            except KeyError, ValueError, TypeError, OverflowError:
                log.warning(
                    "GitHub deployment status row was invalid", extra={"project": ledger.project_id}
                )
        return events

    latest = valid_events(recent)
    events = [*latest, *valid_events(history)]
    if events:
        unseen = await ledger.run("system", lambda uow: _unseen(uow, events))
        for event in unseen:
            await ledger.run("system", partial(_record, branch=branch, event=event))
    if watermark != (cursor.watermark if cursor else "2"):
        progress = FetchCursor(watermark, as_of, cursor.generation + 1 if cursor else 1)
        await ledger.run(
            "system", lambda uow: uow.save_fetch_cursor(api.repository, resource, progress)
        )
    if latest and latest[0].fields["finished"]:
        return None
    environment = deployment.get("environment")
    return {
        "id": identifier,
        "sha": sha,
        "ref": ref if len(ref) <= 1024 else "",
        "environment": environment
        if isinstance(environment, str) and len(environment) <= 255
        else "",
    }


def _status_event(
    repository: str, deployment: Row, identifier: int, sha: str, status: Row
) -> NewEvent:
    status_id = number(status["id"])
    environment = status.get("environment")
    if environment is None or environment == "":
        environment = deployment["environment"]
    fields = deploy_fields(string(environment), sha, string(status["state"]))
    return NewEvent(
        "deploy",
        DEPLOY_STATUS,
        f"github:{repository}:{identifier}:{status_id}",
        {**fields, "repository": repository, "deployment_id": identifier, "status_id": status_id},
    )


async def _unseen(uow: UnitOfWork, events: list[NewEvent]) -> list[NewEvent]:
    return [
        event
        for event in events
        if await uow.find_event(event.source, event.kind, event.upstream_id) is None
    ]


async def _record(uow: UnitOfWork, branch: str, event: NewEvent) -> None:
    lane = await infer_lane(uow, branch=branch) if branch else None
    await uow.append(replace(event, lane_id=lane))
