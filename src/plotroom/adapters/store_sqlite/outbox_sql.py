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
_LEASE = text("""
    UPDATE claude_outbox SET lease_id = :lease_id, expires_at = :expires_at
    WHERE action_key = (
        SELECT action_key FROM claude_outbox WHERE done = 0
          AND (expires_at IS NULL OR expires_at <= :now) ORDER BY seq LIMIT 1
    )
    RETURNING action_key, worker, session_id, message, seq
""")
_COMPLETE = text("""
    UPDATE claude_outbox SET done = 1
    WHERE done = 0 AND lease_id = :lease_id AND expires_at > :now
    RETURNING action_key
""")
_READINESS = text("""
    SELECT MIN(CASE WHEN expires_at IS NULL OR expires_at <= :now THEN seq END) AS ready_seq,
           MIN(CASE WHEN expires_at > :now THEN expires_at END) AS next_expiry
    FROM claude_outbox WHERE done = 0
""")


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


async def readiness(connection: AsyncConnection, head: int, now: str) -> OutboxReadiness:
    row = (await connection.execute(_READINESS, {"now": now})).one()
    return OutboxReadiness(head, row.ready_seq, row.next_expiry)
