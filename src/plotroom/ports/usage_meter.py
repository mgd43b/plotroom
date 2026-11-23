"""The read-only port for a project's cumulative usage observations."""

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Protocol


@dataclass(frozen=True)
class UsageObservation:
    meter: str
    run: str
    spent_usd: float
    as_of: datetime

    def __post_init__(self) -> None:
        try:
            spend = float(self.spent_usd)
        except OverflowError:
            raise ValueError("meter spend must be finite and nonnegative") from None
        if isinstance(self.spent_usd, bool) or not isfinite(spend) or spend < 0:
            raise ValueError("meter spend must be finite and nonnegative")
        if self.as_of.utcoffset() is None:
            raise ValueError("meter timestamps must include a time zone")
        object.__setattr__(self, "spent_usd", spend)
        object.__setattr__(self, "as_of", self.as_of.astimezone(UTC))


class UsageMeter(Protocol):
    async def observations(self) -> tuple[UsageObservation, ...]:
        """Return a snapshot of cumulative readings from the meter."""
        ...
