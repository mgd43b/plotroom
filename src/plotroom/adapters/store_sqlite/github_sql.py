"""Persistence for the project GitHub projection."""

from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.github import PULL_REQUESTS, FetchCursor, GitHubSummary, PullRequest

PULL = text(
    "SELECT * FROM github_pull_requests WHERE repository = :repository AND number = :number"
)
SAVE_PULL = text("""
    INSERT INTO github_pull_requests (repository, number, branch, head_sha, state, updated_at)
    VALUES (:repository, :number, :branch, :head_sha, :state, :updated_at)
    ON CONFLICT (repository, number) DO UPDATE SET branch = excluded.branch,
      head_sha = excluded.head_sha, state = excluded.state, updated_at = excluded.updated_at
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


async def pull_request(
    connection: AsyncConnection, repository: str, number: int
) -> PullRequest | None:
    row = (await connection.execute(PULL, {"repository": repository, "number": number})).first()
    if row is None:
        return None
    return PullRequest(repository, number, row.branch, row.head_sha, row.state, row.updated_at)


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


async def summary(connection: AsyncConnection, repository: str) -> GitHubSummary:
    opened = (await connection.execute(OPEN_COUNT, {"repository": repository})).scalar_one()
    cursor = await fetch_cursor(connection, repository, PULL_REQUESTS)
    return GitHubSummary(int(opened), cursor.as_of if cursor is not None else None)
