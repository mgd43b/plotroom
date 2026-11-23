"""Killing the server mid-transaction leaves no part of that input in the file (AD-14)."""

from __future__ import annotations

import asyncio
import signal
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

from conftest import child_env
from fakes import FixedClock, Fixture
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.events import NewEvent
from plotroom.core.ledger import Ledger

CHILD = Path(__file__).parent / "ledger_child.py"
WAL_HEADER_BYTES = 32
TIMEOUT = 120.0


def read_marker(process: subprocess.Popen[str], name: str) -> int:
    """The number printed after ``name`` by the child, which must print it next."""
    assert process.stdout is not None
    line = process.stdout.readline().strip()
    assert line.startswith(f"{name} "), f"expected {name!r}, the child printed {line!r}"
    return int(line.split()[1])


def test_a_sigkill_inside_a_transaction_leaves_only_the_committed_input(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [sys.executable, str(CHILD), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=child_env(),
    )
    watchdog = threading.Timer(TIMEOUT, process.kill)  # a stuck child must not hang the suite
    watchdog.start()
    try:
        committed = read_marker(process, "COMMITTED")
        mid = read_marker(process, "MID")
        # uncommitted pages are already in the WAL: the kill lands with the transaction half written
        assert mid > WAL_HEADER_BYTES
        assert mid >= committed + 2_000_000
        process.send_signal(signal.SIGKILL)
        assert process.wait(timeout=60) == -signal.SIGKILL
    finally:
        watchdog.cancel()
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdout is not None:
            process.stdout.close()
    assert (tmp_path / "demo.db-wal").stat().st_size >= committed + 2_000_000

    # reopened with plain sqlite3: only the committed input, and the counter at its value
    with sqlite3.connect(tmp_path / "demo.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("SELECT seq, kind, upstream_id FROM events ORDER BY seq").fetchall() == [
            (1, "fixture.noted", "committed")
        ]
        assert db.execute("SELECT value FROM project_seq").fetchall() == [(1,)]
    asyncio.run(append_after_restart(tmp_path))


async def append_after_restart(directory: Path) -> None:
    """A fresh store and ledger on the recovered file take the next input at the next ``seq``."""
    store = SqliteStore("demo", directory)
    await store.migrate()
    ledger = Ledger(store, Fixture().catalog, FixedClock())
    ledger.start()
    try:
        assert await ledger.head_seq() == 1
        appended = await ledger.append(
            "system", NewEvent("fixture", "fixture.noted", "after-the-kill", {"text": "new"})
        )
        assert (appended.created, appended.event.seq) == (True, 2)
        # the killed command left nothing: it runs for the first time now (no dedupe record)
        again = await ledger.append(
            "system", NewEvent("fixture", "fixture.noted", "big-0", {"text": "big"})
        )
        assert (again.created, again.event.seq) == (True, 3)
        assert await ledger.head_seq() == 3
    finally:
        await ledger.stop()
        await store.close()
