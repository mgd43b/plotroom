"""Persistence for the project GitHub projection."""

from dataclasses import asdict, replace
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.github import PULL_REQUESTS, FetchCursor, FetchRetry, GitHubSummary, PullRequest
from plotroom.core.lanes import Lane

PULL = text(
    "SELECT * FROM github_pull_requests WHERE repository = :repository AND number = :number"
)
SAVE_PULL = text("""
    INSERT INTO github_pull_requests
      (repository, number, branch, head_sha, head_repository, state, updated_at,
       as_of, complete, version)
    VALUES (
      :repository, :number, :branch, :head_sha, :head_repository, :state, :updated_at,
      :as_of, :complete, :version
    )
    ON CONFLICT (repository, number) DO UPDATE SET branch = excluded.branch,
      head_sha = excluded.head_sha, state = excluded.state, updated_at = excluded.updated_at,
      head_repository = excluded.head_repository,
      as_of = excluded.as_of, complete = excluded.complete, version = excluded.version
""")
CURSOR = text("""
    SELECT watermark, as_of, generation FROM github_fetch_cursors
    WHERE repository = :repository AND resource = :resource
""")
SAVE_CURSOR = text("""
    INSERT INTO github_fetch_cursors (repository, resource, watermark, as_of, generation)
    VALUES (:repository, :resource, :watermark, :as_of, :generation)
    ON CONFLICT (repository, resource) DO UPDATE SET watermark = excluded.watermark,
      as_of = excluded.as_of, generation = excluded.generation
""")
OPEN_COUNT = text("""
    SELECT count(*) FROM github_pull_requests WHERE repository = :repository AND state = 'open'
""")
BRANCH_HEAD = text("""
    SELECT * FROM github_pull_requests
    WHERE repository = :repository AND head_repository = :repository AND branch = :branch
      AND state = 'open' AND complete
    LIMIT 2
""")
RETRY = text("""
    SELECT until, next_backoff FROM github_fetch_retries
    WHERE repository = :repository AND resource = :resource
""")
SAVE_RETRY = text("""
    INSERT INTO github_fetch_retries (repository, resource, until, next_backoff)
    VALUES (:repository, :resource, :until, :next_backoff)
    ON CONFLICT (repository, resource) DO UPDATE SET until = excluded.until,
      next_backoff = excluded.next_backoff
""")
CLEAR_RETRY = text("""
    DELETE FROM github_fetch_retries WHERE repository = :repository AND resource = :resource
""")


async def pull_request(
    connection: AsyncConnection, repository: str, number: int
) -> PullRequest | None:
    row = (await connection.execute(PULL, {"repository": repository, "number": number})).first()
    if row is None:
        return None
    return _pull_from_row(row)


def _pull_from_row(row: Row[Any]) -> PullRequest:
    return PullRequest(
        row.repository,
        row.number,
        row.branch,
        row.head_sha,
        row.head_repository,
        row.state,
        row.updated_at,
        row.as_of,
        bool(row.complete),
        row.version,
    )


async def open_branch_pull(
    connection: AsyncConnection, repository: str, branch: str
) -> PullRequest | None:
    rows = (
        await connection.execute(BRANCH_HEAD, {"repository": repository, "branch": branch})
    ).all()
    return _pull_from_row(rows[0]) if len(rows) == 1 else None


async def project_lane_heads(
    connection: AsyncConnection, repository: str, lanes: list[Lane]
) -> list[Lane]:
    projected: list[Lane] = []
    for lane in lanes:
        pr = await open_branch_pull(connection, repository, lane.branch)
        if pr is not None:
            projected.append(replace(lane, pr=pr.number, head_sha=pr.head_sha))
        else:
            projected.append(replace(lane, pr=None, head_sha=None))
    return projected


async def save_pull_request(connection: AsyncConnection, pull: PullRequest) -> None:
    await connection.execute(SAVE_PULL, asdict(pull))


async def fetch_cursor(
    connection: AsyncConnection, repository: str, resource: str
) -> FetchCursor | None:
    row = (
        await connection.execute(CURSOR, {"repository": repository, "resource": resource})
    ).first()
    return FetchCursor(row.watermark, row.as_of, row.generation) if row is not None else None


async def save_fetch_cursor(
    connection: AsyncConnection, repository: str, resource: str, cursor: FetchCursor
) -> None:
    await connection.execute(
        SAVE_CURSOR, {"repository": repository, "resource": resource, **asdict(cursor)}
    )


async def fetch_retry(
    connection: AsyncConnection, repository: str, resource: str
) -> FetchRetry | None:
    row = (
        await connection.execute(RETRY, {"repository": repository, "resource": resource})
    ).first()
    return FetchRetry(row.until, row.next_backoff) if row is not None else None


async def save_fetch_retry(
    connection: AsyncConnection, repository: str, resource: str, retry: FetchRetry | None
) -> None:
    keys = {"repository": repository, "resource": resource}
    if retry is None:
        await connection.execute(CLEAR_RETRY, keys)
    else:
        await connection.execute(SAVE_RETRY, {**keys, **asdict(retry)})


async def summary(connection: AsyncConnection, repository: str) -> GitHubSummary:
    opened = (await connection.execute(OPEN_COUNT, {"repository": repository})).scalar_one()
    cursor = await fetch_cursor(connection, repository, PULL_REQUESTS)
    return GitHubSummary(int(opened), cursor.as_of if cursor is not None else None)
