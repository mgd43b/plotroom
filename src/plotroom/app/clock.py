"""The system ``Clock``: the one place the server reads the time."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Implements ``plotroom.ports.Clock`` with the wall clock, in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)
