"""Poll explicitly bound local sessions and submit their observations to core."""

import asyncio
import logging
from collections.abc import Mapping

from plotroom.core.events import LedgerError
from plotroom.core.intake import record_claude
from plotroom.core.lanes import infer_lane
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.session_reader import SessionEvent, SessionReader

log = logging.getLogger("plotroom.session_intake")


class SessionIntake:
    def __init__(
        self, ledger: Ledger, reader: SessionReader, sessions: Mapping[str, str], seconds: int
    ) -> None:
        self._ledger = ledger
        self._reader = reader
        self._sessions = sessions
        self._seconds = seconds

    async def poll(self) -> None:
        for session, worker in self._sessions.items():
            observed = await self._reader.read(session)
            if not observed or await self._record(worker, observed):
                self._reader.accept(session)

    async def _record(self, worker: str, events: tuple[SessionEvent, ...]) -> bool:
        async def record(uow: UnitOfWork) -> bool:
            lane = await infer_lane(uow, worker=worker)
            if lane is None:
                return False
            for event in events:
                try:
                    await record_claude(uow, event, lane=lane)
                except LedgerError as exc:
                    if exc.code != "invalid":
                        raise
                    log.warning("invalid Desktop observation", extra={"kind": event.kind})
            return True

        return await self._ledger.run("system", record)

    async def run(self) -> None:
        while True:
            await self.poll()
            await asyncio.sleep(self._seconds)
