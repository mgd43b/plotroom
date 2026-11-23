"""The ``Clock`` port: core's only source of time (AD-2)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current moment as a timezone-aware ``datetime``."""
        ...
