"""Persistence for the project GitHub projection."""

import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.github import (
    MERGE_SHA_ATTEMPT_PREFIX,
    PULL_REQUESTS,
    FetchCursor,
    FetchRetry,
    GitHubSummary,
    PullRequest,
)
from plotroom.core.github_projection import GitHubRecord, GitHubResource
from plotroom.core.lanes import Lane, with_projected_pr

PULL = text(
    "SELECT * FROM github_pull_requests WHERE repository = :repository AND number = :number"
)
SAVE_PULL = text("""
    INSERT INTO github_pull_requests
      (repository, number, branch, head_sha, head_repository, state, updated_at,
       as_of, complete, version, draft, merge_commit_sha)
    VALUES (
      :repository, :number, :branch, :head_sha, :head_repository, :state, :updated_at,
      :as_of, :complete, :version, :draft, :merge_commit_sha
    )
    ON CONFLICT (repository, number) DO UPDATE SET branch = excluded.branch,
      head_sha = excluded.head_sha, state = excluded.state, updated_at = excluded.updated_at,
      head_repository = excluded.head_repository,
      as_of = excluded.as_of, complete = excluded.complete, version = excluded.version,
      draft = excluded.draft, merge_commit_sha = excluded.merge_commit_sha
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
BRANCH_HEADS = text("""
    SELECT pr.branch, MIN(pr.number) AS number, MIN(pr.head_sha) AS head_sha
    FROM github_pull_requests AS pr
    JOIN json_each(:branches) AS requested ON pr.branch = requested.value
    WHERE pr.repository = :repository AND pr.head_repository = :repository
      AND pr.state = 'open' AND pr.complete
    GROUP BY pr.branch HAVING COUNT(*) = 1
""")
PENDING_CHECKLIST_HEADS = text("""
    SELECT checklist.lane, checklist.pr, checklist.head_sha
    FROM json_each(:ready_lanes) AS requested
    JOIN lane_checklists AS checklist ON checklist.lane = requested.value
    JOIN github_pull_requests AS pr
      ON pr.repository = checklist.repository AND pr.number = checklist.pr
     AND pr.head_repository = checklist.repository AND pr.branch = checklist.branch
     AND pr.head_sha = checklist.head_sha
    WHERE checklist.repository = :repository AND checklist.state = 'pending'
      AND pr.state = 'open' AND pr.complete
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
    return pull_from_row(row)


def pull_from_row(row: Row[Any]) -> PullRequest:
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
        None if row.draft is None else bool(row.draft),
        row.merge_commit_sha,
    )


async def open_branch_pull(
    connection: AsyncConnection, repository: str, branch: str
) -> PullRequest | None:
    rows = (
        await connection.execute(BRANCH_HEAD, {"repository": repository, "branch": branch})
    ).all()
    return pull_from_row(rows[0]) if len(rows) == 1 else None


async def project_lane_heads(
    connection: AsyncConnection, repository: str, lanes: list[Lane]
) -> list[Lane]:
    if not lanes:
        return []
    branches = json.dumps(sorted({lane.branch for lane in lanes}))
    rows = await connection.execute(BRANCH_HEADS, {"repository": repository, "branches": branches})
    matches: dict[str, tuple[int | None, str | None]] = {
        row.branch: (row.number, row.head_sha) for row in rows
    }
    pending: dict[str, tuple[int, str]] = {}
    ready_lanes = [lane.id for lane in lanes if lane.state == "ready"]
    if ready_lanes:
        headers = await connection.execute(
            PENDING_CHECKLIST_HEADS,
            {"repository": repository, "ready_lanes": json.dumps(ready_lanes)},
        )
        pending = {row.lane: (row.pr, row.head_sha) for row in headers}
    return [
        with_projected_pr(lane, *pending.get(lane.id, matches.get(lane.branch, (None, None))))
        for lane in lanes
    ]


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


RESOURCE = text("""
    SELECT * FROM github_resources
    WHERE repository = :repository AND resource = :resource AND scope = :scope
""")
SAVE_RESOURCE = text("""
    INSERT INTO github_resources
      (repository, resource, scope, records, attributes, complete, generation, as_of)
    VALUES
      (:repository, :resource, :scope, :records, :attributes, :complete, :generation, :as_of)
    ON CONFLICT (repository, resource, scope) DO UPDATE SET records = excluded.records,
      attributes = excluded.attributes, complete = excluded.complete,
      generation = excluded.generation, as_of = excluded.as_of
""")
PULL_TARGETS = text("""
    SELECT pr.* FROM github_pull_requests AS pr
    LEFT JOIN github_fetch_cursors AS cursor
      ON cursor.repository = pr.repository AND cursor.resource = 'pull_details/' || pr.number
    WHERE pr.repository = :repository AND pr.complete
      AND (
        pr.state = 'open' OR cursor.watermark IS NULL
        OR cursor.watermark != (pr.updated_at || ':' || pr.version)
      )
    ORDER BY COALESCE(cursor.as_of, ''), pr.updated_at DESC, pr.number
    LIMIT 8
""")
PENDING_MERGE_SHAS = text("""
    SELECT pr.* FROM github_pull_requests AS pr
    LEFT JOIN github_fetch_cursors AS attempt
      ON attempt.repository = pr.repository AND attempt.resource = :prefix || pr.number
    WHERE pr.repository = :repository AND pr.complete AND pr.state = 'merged'
      AND pr.merge_commit_sha IS NULL
    ORDER BY MIN(COALESCE(attempt.as_of, pr.updated_at), :as_of),
             COALESCE(attempt.generation, 0), pr.number
    LIMIT :limit
""")
OPEN_PULLS = text(
    "SELECT * FROM github_pull_requests WHERE repository = :repository "
    "AND complete AND state = 'open' ORDER BY number"
)


async def open_pulls(connection: AsyncConnection, repository: str) -> tuple[PullRequest, ...]:
    rows = await connection.execute(OPEN_PULLS, {"repository": repository})
    return tuple(pull_from_row(row) for row in rows)


async def github_resource(
    connection: AsyncConnection, repository: str, resource: str, scope: str
) -> GitHubResource | None:
    row = (
        await connection.execute(
            RESOURCE, {"repository": repository, "resource": resource, "scope": scope}
        )
    ).first()
    if row is None:
        return None
    return resource_from_row(row)


def resource_from_row(row: Row[Any]) -> GitHubResource:
    return GitHubResource(
        tuple(GitHubRecord(**record) for record in json.loads(row.records)),
        bool(row.complete),
        json.loads(row.attributes),
        row.generation,
        row.as_of,
    )


async def save_github_resource(
    connection: AsyncConnection, repository: str, resource: str, scope: str, result: GitHubResource
) -> None:
    await connection.execute(
        SAVE_RESOURCE,
        {
            "repository": repository,
            "resource": resource,
            "scope": scope,
            "records": json.dumps([asdict(record) for record in result.records]),
            "attributes": json.dumps(result.attributes),
            "complete": result.complete,
            "generation": result.generation,
            "as_of": result.as_of,
        },
    )


async def github_pull_targets(
    connection: AsyncConnection, repository: str
) -> tuple[PullRequest, ...]:
    rows = await connection.execute(PULL_TARGETS, {"repository": repository})
    return tuple(pull_from_row(row) for row in rows)


async def github_pending_merge_shas(
    connection: AsyncConnection, repository: str, as_of: str, limit: int
) -> tuple[PullRequest, ...]:
    rows = await connection.execute(
        PENDING_MERGE_SHAS,
        {
            "repository": repository,
            "prefix": MERGE_SHA_ATTEMPT_PREFIX,
            "as_of": as_of,
            "limit": limit,
        },
    )
    return tuple(pull_from_row(row) for row in rows)
