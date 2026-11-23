"""Claude-shaped outbox records and writer-owned delivery leases."""

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from plotroom.core.events import LedgerError, format_time

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

LEASE_DURATION = timedelta(seconds=60)


@dataclass(frozen=True)
class OutboxMessage:
    action_key: str
    worker: str
    session_id: str
    text: str
    seq: int


@dataclass(frozen=True)
class OutboxLease:
    message: OutboxMessage
    act_id: str
    expires_at: str

    def render(self) -> dict[str, Any]:
        return {**asdict(self.message), "act_id": self.act_id, "expires_at": self.expires_at}


@dataclass(frozen=True)
class OutboxReadiness:
    head: int
    ready_seq: int | None
    next_expiry: str | None = None


def lease_request_id(consumer_id: str, request_id: str) -> str:
    """Scope retries to one drain process even if it shares a project drain token."""
    try:
        consumer = UUID(consumer_id)
    except ValueError:
        raise LedgerError("invalid", "consumer_id must be a UUIDv4") from None
    if consumer.version != 4:
        raise LedgerError("invalid", "consumer_id must be a UUIDv4")
    if not request_id:
        raise LedgerError("invalid", "outbox_lease requires a nonempty request_id")
    return f"{consumer}:{request_id}"


async def lease_outbox(uow: UnitOfWork, _args: dict[str, Any]) -> dict[str, Any]:
    now = uow.now()
    lease = await uow.lease_outbox(
        f"lease_{uuid4()}", format_time(now), format_time(now + LEASE_DURATION)
    )
    return {"lease": None if lease is None else lease.render()}


async def complete_outbox(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    act_id = args["act_id"]
    if not act_id:
        raise LedgerError("invalid", "act_id must be nonempty")
    key = await uow.complete_outbox(act_id, format_time(uow.now()))
    if key is None:
        raise LedgerError("conflict", "act_id does not identify a current outbox lease")
    return {"action_key": key, "state": "done"}
