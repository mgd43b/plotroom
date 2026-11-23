import json
from pathlib import Path

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.catalog import build_catalog
from plotroom.core.ledger import Ledger
from plotroom.core.next_items import ItemKind
from plotroom.core.snapshot import estimate_tokens, render_snapshot


async def test_rank_dedup_ack_and_snapshot_budget(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    catalog = build_catalog()
    catalog.register_item(ItemKind("fixture_state", 20, True, ("Reply",)))
    catalog.register_item(ItemKind("fixture_event", 10, False, ("Inspect",)))
    ledger = Ledger(store, catalog, FixedClock())
    ledger.start()
    try:
        state = await ledger.run(
            "system", lambda uow: uow.raise_item("fixture_state", "a", "orchestrator", {})
        )
        event = await ledger.run(
            "system",
            lambda uow: uow.raise_item("fixture_event", "b", "orchestrator", {"body": "x" * 2048}),
        )
        same = await ledger.run(
            "system", lambda uow: uow.raise_item("fixture_event", "b", "orchestrator", {})
        )
        assert same == event
        cursor, items, lanes = await ledger.read_board("orchestrator")
        assert cursor == 2
        assert items == [event, state]
        board = render_snapshot("demo", cursor, items, lanes, 100)
        assert board["left_out"] == 2
        assert board["open_items"] == {"fixture_event": 1, "fixture_state": 1}
        assert estimate_tokens(json.dumps(board)) <= 100
        for item, expected in [(event, "closed"), (state, "snoozed")]:
            first = await ledger.execute("orchestrator", "ack", f"{item.id}:1", {"item": item.id})
            after = await ledger.head_seq()
            second = await ledger.execute("orchestrator", "ack", f"{item.id}:2", {"item": item.id})
            assert first.result == {"item": item.id, "state": expected, "changed": True}
            assert second.result == {**first.result, "changed": False}
            assert await ledger.head_seq() == after
        assert (await ledger.read_board("orchestrator"))[1] == []
        changed = await ledger.run(
            "system",
            lambda uow: uow.raise_item("fixture_state", "a", "orchestrator", {"new": True}),
        )
        assert changed.id == state.id
        assert changed.state == "open"
        await ledger.run("system", lambda uow: uow.clear_item("fixture_state", "a"))
        assert (await ledger.read_board("orchestrator"))[1] == []
    finally:
        await ledger.stop()
        await store.close()
