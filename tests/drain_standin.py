"""A synthetic native receiver that deduplicates deliveries by action key."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class DrainStandIn:
    deliveries: dict[str, tuple[str, str]] = field(default_factory=lambda: {})
    consumer_id: str = field(default_factory=lambda: str(uuid4()))

    def deliver(self, lease: dict[str, Any]) -> None:
        key = lease["action_key"]
        receipt = (lease["session_id"], lease["text"])
        assert self.deliveries.setdefault(key, receipt) == receipt

    async def drain_one(self, call: Callable[..., Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        wake = await call("wait", timeout=5)
        assert wake["item"]["kind"] == "outbox_ready"
        leased = await call("outbox_lease", consumer_id=self.consumer_id)
        lease: dict[str, Any] = leased["lease"]
        self.deliver(lease)
        assert await call("outbox_done", act_id=lease["act_id"]) == {
            "action_key": lease["action_key"],
            "state": "done",
        }
        return lease
