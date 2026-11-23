"""Persistence for the local Claude outbox."""

from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.outbox import OutboxLease, OutboxMessage, OutboxReadiness

_INSERT = text("""
    INSERT INTO claude_outbox (action_key, worker, session_id, message, seq)
    VALUES (:action_key, :worker, :session_id, :text, :seq)
    ON CONFLICT(action_key) DO NOTHING
""")
# A budget stop is addressed to a particular live lane, even after it enters the outbox.
_DELIVERABLE = """
    (o.action_key NOT GLOB 'budget:*' OR EXISTS (
        SELECT 1 FROM actions AS a JOIN lanes AS l ON l.id = json_extract(a.payload, '$.lane')
        WHERE a.action_key = o.action_key AND l.worker = o.worker AND l.state != 'archived'
    ))
"""
_LEASE = text(f"""
    UPDATE claude_outbox SET lease_id = :lease_id, expires_at = :expires_at
    WHERE action_key = (
        SELECT o.action_key FROM claude_outbox AS o WHERE done = 0
          AND (expires_at IS NULL OR expires_at <= :now) AND {_DELIVERABLE}
        ORDER BY seq LIMIT 1
    )
    RETURNING action_key, worker, session_id, message, seq
""")  # noqa: S608 - fixed SQL predicate
_COMPLETE = text("""
    UPDATE claude_outbox SET done = 1
    WHERE done = 0 AND lease_id = :lease_id AND expires_at > :now
    RETURNING action_key
""")
_READINESS = text(f"""
    SELECT MIN(CASE WHEN expires_at IS NULL OR expires_at <= :now THEN seq END) AS ready_seq,
           MIN(CASE WHEN expires_at > :now THEN expires_at END) AS next_expiry
    FROM claude_outbox AS o WHERE done = 0 AND {_DELIVERABLE}
""")  # noqa: S608 - fixed SQL predicate
_RETIRE_STALE = text(f"""
    UPDATE claude_outbox AS o SET done = 1
    WHERE done = 0 AND (expires_at IS NULL OR expires_at <= :now) AND NOT {_DELIVERABLE}
""")  # noqa: S608 - fixed SQL predicate


async def insert_outbox(connection: AsyncConnection, message: OutboxMessage) -> None:
    await connection.execute(_INSERT, asdict(message))


async def lease_outbox(
    connection: AsyncConnection, lease_id: str, now: str, expires_at: str
) -> OutboxLease | None:
    row = (
        await connection.execute(
            _LEASE, {"lease_id": lease_id, "now": now, "expires_at": expires_at}
        )
    ).first()
    if row is None:
        return None
    message = OutboxMessage(row.action_key, row.worker, row.session_id, row.message, row.seq)
    return OutboxLease(message, lease_id, expires_at)


async def complete_outbox(connection: AsyncConnection, lease_id: str, now: str) -> str | None:
    key = (
        await connection.execute(_COMPLETE, {"lease_id": lease_id, "now": now})
    ).scalar_one_or_none()
    return None if key is None else str(key)


async def retire_stale_outbox(connection: AsyncConnection, now: str) -> None:
    await connection.execute(_RETIRE_STALE, {"now": now})


async def readiness(connection: AsyncConnection, head: int, now: str) -> OutboxReadiness:
    row = (await connection.execute(_READINESS, {"now": now})).one()
    return OutboxReadiness(head, row.ready_seq, row.next_expiry)
