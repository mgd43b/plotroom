"""``plotroom serve`` owns one running ledger per project and stops it before the lock goes."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from pathlib import Path

import pytest

from fakes import FixedClock, Fixture
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app import serve
from plotroom.app.lock import DataDirLock, LockError
from plotroom.app.serve import Daemon
from plotroom.core.catalog import Catalog
from plotroom.core.events import LedgerError, NewEvent
from plotroom.core.ledger import Ledger
from plotroom.core.trigger_actions import DeliveryRoute
from plotroom.core.unit_of_work import UnitOfWork
from test_daemon import make_config, run_daemon, stop


@pytest.fixture(autouse=True)
def fixture_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    def build_fixture_catalog(
        _workers: frozenset[str],
        *,
        github_repository: str | None = None,
        delivery_routes: Mapping[str, DeliveryRoute] | None = None,
        image_jobs: tuple[str, ...] = (),
        lane_idle_minutes: int = 15,
        human_login: str | None = None,
    ) -> Catalog:
        return Fixture().catalog

    monkeypatch.setattr(serve, "build_catalog", build_fixture_catalog)


def note(key: str) -> NewEvent:
    return NewEvent("fixture", "fixture.noted", key, {"text": key})


def file_events(directory: Path, project: str = "demo") -> list[tuple[int, str]]:
    with sqlite3.connect(directory / f"{project}.db") as db:
        return [
            (row[0], row[1])
            for row in db.execute("SELECT seq, upstream_id FROM events ORDER BY seq")
        ]


def spy_on_teardown(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the order in which ledgers stop, stores close and the lock is released."""
    order: list[str] = []
    stop_ledger = Ledger.stop
    close_store = SqliteStore.close
    release = DataDirLock.release

    async def spied_stop(self: Ledger) -> None:
        await stop_ledger(self)
        order.append(f"ledger stopped: {self.project_id}")

    async def spied_close(self: SqliteStore) -> None:
        await close_store(self)
        order.append(f"store closed: {self.project_id}")

    def spied_release(self: DataDirLock) -> None:
        order.append("lock released")
        release(self)

    monkeypatch.setattr(Ledger, "stop", spied_stop)
    monkeypatch.setattr(SqliteStore, "close", spied_close)
    monkeypatch.setattr(DataDirLock, "release", spied_release)
    return order


async def test_a_running_server_has_a_working_ledger_per_project(tmp_path: Path) -> None:
    config = make_config(tmp_path, "alpha", "beta")
    daemon = Daemon(config)
    task, _ = await run_daemon(daemon)
    try:
        assert sorted(daemon.ledgers) == ["alpha", "beta"]
        assert (await daemon.ledgers["alpha"].append("system", note("a1"))).event.seq == 1
        assert (await daemon.ledgers["alpha"].append("system", note("a2"))).event.seq == 2
        assert (await daemon.ledgers["beta"].append("system", note("b1"))).event.seq == 1
        # committed state is in each project's own file; nothing crosses
        assert file_events(config.data_dir, "alpha") == [(1, "a1"), (2, "a2")]
        assert file_events(config.data_dir, "beta") == [(1, "b1")]
    finally:
        await stop(daemon, task)
    assert daemon.ledgers == {}


async def test_old_sqlite_starts_no_writer_and_opens_no_project_file(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    daemon = Daemon(config, sqlite_version="3.50.4")
    task, _ = await run_daemon(daemon)
    try:
        assert daemon.ledgers == {}
        assert not (config.data_dir / "demo.db").exists()
    finally:
        await stop(daemon, task)


async def test_stop_stops_every_writer_and_closes_every_store_before_the_lock_goes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = spy_on_teardown(monkeypatch)
    daemon = Daemon(make_config(tmp_path, "alpha", "beta"))
    task, _ = await run_daemon(daemon)
    daemon.stop()
    await asyncio.wait_for(task, timeout=30)
    assert order[-1] == "lock released"
    assert sorted(order[:-1]) == [
        "ledger stopped: alpha",
        "ledger stopped: beta",
        "store closed: alpha",
        "store closed: beta",
    ]
    for project in ("alpha", "beta"):  # a writer stops before its store closes
        assert order.index(f"ledger stopped: {project}") < order.index(f"store closed: {project}")


async def test_canceling_the_server_stops_every_writer_and_closes_every_store_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = spy_on_teardown(monkeypatch)
    config = make_config(tmp_path, "alpha", "beta")
    daemon = Daemon(config)
    task, _ = await run_daemon(daemon)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert order[-1] == "lock released"
    assert len([line for line in order if line.startswith("ledger stopped")]) == 2
    assert len([line for line in order if line.startswith("store closed")]) == 2
    again = Daemon(config)  # the directory is free and the files reopen cleanly
    task2, _ = await run_daemon(again)
    await stop(again, task2)


async def test_the_lock_is_held_while_a_writer_is_still_draining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    draining = asyncio.Event()
    stop_ledger = Ledger.stop

    async def noting_stop(self: Ledger) -> None:
        draining.set()  # the server has begun stopping this writer
        await stop_ledger(self)

    monkeypatch.setattr(Ledger, "stop", noting_stop)
    config = make_config(tmp_path)
    daemon = Daemon(config)
    task, _ = await run_daemon(daemon)
    ledger = daemon.ledgers["demo"]
    release, running = asyncio.Event(), asyncio.Event()

    async def slow(uow: UnitOfWork) -> None:
        await uow.append(note("in flight"))
        running.set()
        await release.wait()
        await uow.append(note("finished"))

    in_flight = asyncio.create_task(ledger.run("system", slow))
    await running.wait()
    queued = asyncio.create_task(ledger.append("system", note("queued")))
    await asyncio.sleep(0)
    daemon.stop()
    await asyncio.wait_for(draining.wait(), timeout=30)
    task.cancel()  # a cancel in the middle of the drain must wait for the writer too
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done(), "the server must wait for its writer to drain"
    with pytest.raises(LockError):  # the directory is still owned
        await Daemon(config).run()
    with pytest.raises(LedgerError) as refused:  # no new input is accepted while draining
        await ledger.append("system", note("too late"))
    assert refused.value.code == "unavailable"
    release.set()
    await in_flight
    assert (await queued).created is True
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=30)
    assert [key for _, key in file_events(config.data_dir)] == ["in flight", "finished", "queued"]
    again = Daemon(config)
    task2, _ = await run_daemon(again)
    await stop(again, task2)


async def test_a_project_that_fails_to_migrate_does_not_leave_the_others_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = spy_on_teardown(monkeypatch)
    config = make_config(tmp_path, "alpha", "beta")
    config.data_dir.mkdir(parents=True)
    with sqlite3.connect(config.data_dir / "beta.db") as db:
        db.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        db.execute("INSERT INTO alembic_version VALUES ('from-the-future')")
    with pytest.raises(Exception, match="from-the-future"):
        await Daemon(config).run()
    assert order[-1] == "lock released"
    assert "ledger stopped: alpha" in order
    assert {"store closed: alpha", "store closed: beta"} <= set(order)
    assert "ledger stopped: beta" not in order  # it never started
    again = DataDirLock(config.data_dir)
    again.acquire()  # the lock was released
    again.release()


async def test_the_clock_the_server_was_given_stamps_its_events(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    daemon = Daemon(config, clock=FixedClock())
    task, _ = await run_daemon(daemon)
    try:
        appended = await daemon.ledgers["demo"].append("system", note("stamped"))
        assert appended.event.observed_at == "2026-11-22T12:00:00.000000Z"
    finally:
        await stop(daemon, task)
