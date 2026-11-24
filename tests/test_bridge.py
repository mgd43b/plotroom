from pathlib import Path

import pytest

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.actions import SWITCHBOARD_SEND
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError, NewEvent
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork


async def test_bridge_leases_survive_expiry_and_reuse_the_action_key(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    ledger = Ledger(store, build_catalog(bridge_board="fixture"), clock, bridge_workers=())
    ledger.start()

    async def enqueue(uow: UnitOfWork) -> str:
        cause = (await uow.append(NewEvent("core", "core.tick", "fixture"))).event
        return await uow.enqueue_action(cause, SWITCHBOARD_SEND, {"to": "peer", "body": "fixture"})

    async def no_progress(_elapsed: float, _cap: float) -> None:
        pass

    try:
        key = await ledger.run("system", enqueue)
        cursor, item = await ledger.wait_for_item("bridge", None, 0, no_progress)
        assert item is not None
        assert item["kind"] == "bridge_ready"
        first = (await ledger.execute("bridge", "bridge_lease", "first", {})).result["lease"]
        assert first["action_key"] == key
        assert (await ledger.execute("bridge", "bridge_lease", "second", {})).result[
            "lease"
        ] is None
        assert (await ledger.wait_for_item("bridge", cursor, 0, no_progress))[1] is None
        clock.advance(61)
        _, item = await ledger.wait_for_item("bridge", cursor, 0, no_progress)
        assert item is not None
        assert item["kind"] == "bridge_ready"
        retry = (await ledger.execute("bridge", "bridge_lease", "retry", {})).result["lease"]
        assert retry["action_key"] == key
        assert retry["act_id"] != first["act_id"]
        with pytest.raises(LedgerError, match="current bridge lease"):
            await ledger.execute("bridge", "bridge_done", "stale", {"act_id": first["act_id"]})
        assert (
            await ledger.execute("bridge", "bridge_done", "current", {"act_id": retry["act_id"]})
        ).result == {"action_key": key, "state": "done"}
        assert await ledger.pending_action() is None
    finally:
        await ledger.stop()
        await store.close()
