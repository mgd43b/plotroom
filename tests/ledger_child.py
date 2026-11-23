"""Child of ``test_kill_mid_transaction``: stops inside a transaction whose pages reached the WAL.

Usage: ``python ledger_child.py <data_dir>``. It commits one input and prints ``COMMITTED <wal
bytes>``. It then runs a command whose handler appends events with large bodies until the WAL has
grown by megabytes (SQLite spills dirty pages of a big transaction to the WAL before commit),
prints ``MID <wal bytes>`` and waits forever with that transaction still open. The parent
kills it there. ``NOSPILL`` (exit 3) means the pages never reached the WAL.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.clock import SystemClock
from plotroom.core.catalog import build_catalog
from plotroom.core.commands import Command
from plotroom.core.events import EventKind, FieldSpec, NewEvent
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork

BODY = "x" * 1_000_000
SPILL_BYTES = 4_000_000
MAX_EVENTS = 64


async def main(data_dir: Path) -> int:
    wal = data_dir / "demo.db-wal"
    catalog = build_catalog()
    catalog.events.register(
        EventKind("fixture.noted", (FieldSpec("text", "str"),), frozenset({"system"}))
    )
    committed = 0

    async def bulk(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
        for index in range(MAX_EVENTS):
            await uow.append(
                NewEvent("fixture", "fixture.noted", f"big-{index}", {"text": "big"}, BODY)
            )
            if wal.stat().st_size >= committed + SPILL_BYTES:
                print(f"MID {wal.stat().st_size}", flush=True)
                await asyncio.Event().wait()  # holds the transaction open until killed
        print("NOSPILL", flush=True)
        sys.exit(3)

    catalog.commands.register(Command("bulk", (), bulk))
    store = SqliteStore("demo", data_dir)
    await store.migrate()
    ledger = Ledger(store, catalog, SystemClock())
    ledger.start()
    await ledger.append(
        "system", NewEvent("fixture", "fixture.noted", "committed", {"text": "kept"})
    )
    committed = wal.stat().st_size
    print(f"COMMITTED {committed}", flush=True)
    await ledger.execute("orchestrator", "bulk", "req-1", {})
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
