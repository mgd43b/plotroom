"""Track live wait requests and derive missing-watcher items on ticks."""

from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

MISSING_WATCHER = "watcher_not_running"


class Watchers:
    def __init__(self, roles: tuple[str, ...]) -> None:
        self.roles = roles
        self._active: Counter[str] = Counter()

    @contextmanager
    def waiting(self, role: str) -> Generator[None]:
        self._active[role] += 1
        try:
            yield
        finally:
            self._active[role] -= 1
            if not self._active[role]:
                del self._active[role]

    async def reconcile(self, uow: UnitOfWork) -> None:
        roles = set(self.roles)
        for role in sorted(roles | await uow.active_item_subjects(MISSING_WATCHER)):
            if role not in roles or self._active[role]:
                await uow.clear_item(MISSING_WATCHER, role)
            else:
                await uow.raise_item(MISSING_WATCHER, role, "orchestrator", {"role": role})
