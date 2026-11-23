"""Poll explicitly bound local sessions and submit their observations to core."""

import asyncio
from collections.abc import Mapping

from plotroom.core.intake import record_claude
from plotroom.core.lanes import infer_lane
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.session_reader import SessionEvent, SessionReader


class SessionIntake:
    def __init__(
        self, ledger: Ledger, reader: SessionReader, sessions: Mapping[str, str], seconds: int
    ) -> None:
        self._ledger = ledger
        self._reader = reader
        self._sessions = sessions
        self._seconds = seconds
        self._seen: dict[str, set[SessionEvent]] = {}

    async def poll(self) -> None:
        for session, worker in self._sessions.items():
            observed = await self._reader.read(session)
            seen = self._seen.setdefault(session, set())
            pending = tuple(event for event in observed if event not in seen)
            if pending and await self._record(worker, pending):
                seen.update(pending)

    async def _record(self, worker: str, events: tuple[SessionEvent, ...]) -> bool:
        async def record(uow: UnitOfWork) -> bool:
            lane = await infer_lane(uow, worker=worker)
            if lane is None:
                return False
            for event in events:
                await record_claude(uow, event, lane=lane)
            return True

        return await self._ledger.run("system", record)

    async def run(self) -> None:
        while True:
            await self.poll()
            await asyncio.sleep(self._seconds)
