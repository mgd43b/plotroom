"""The Switchboard message search projection uses the project writer transaction."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore, search_sql
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError, NewEvent
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.search import SearchHit, SearchPage


def message(upstream_id: str, body: str) -> NewEvent:
    return NewEvent("switchboard", "switchboard.message", upstream_id, {"sender": "fixture"}, body)


@asynccontextmanager
async def opened_search(path: Path, project: str) -> AsyncGenerator[tuple[SqliteStore, Ledger]]:
    store = SqliteStore(project, path)
    await store.migrate()
    ledger = Ledger(store, build_catalog(), FixedClock(), search=store)
    ledger.start()
    try:
        yield store, ledger
    finally:
        await ledger.stop()
        await store.close()


async def test_search_keeps_original_and_follows_commit_and_rollback(tmp_path: Path) -> None:
    async with opened_search(tmp_path, "demo") as (store, ledger):
        body = "The synthetic needle is in this message"
        first = await ledger.append("system", message("first", body))
        replay = await ledger.append("system", message("first", "replacement"))
        assert not replay.created
        assert replay.event == first.event

        async def failed_input(uow: UnitOfWork) -> None:
            await uow.append(message("failed", "never committed"))
            assert await ledger.search("never", None, 20) == SearchPage((), 0)
            raise ValueError("fixture rollback")

        with pytest.raises(ValueError, match="fixture rollback"):
            await ledger.run("system", failed_input)

        await ledger.append("system", NewEvent("core", "core.tick", "other", {}, "needle"))
        assert await ledger.search("NEEDLE", None, 20) == SearchPage(
            (SearchHit(first.event.id, first.event.kind, body),), 1
        )
        assert await ledger.search('"needle"', ("switchboard.message",), 20) == SearchPage(
            (SearchHit(first.event.id, first.event.kind, body),), 1
        )
        assert await ledger.search("needle OR replacement", None, 20) == SearchPage((), 0)
        assert await ledger.search("needle", ("core.tick",), 20) == SearchPage((), 0)
        assert await ledger.search("replacement", None, 20) == SearchPage((), 0)
        assert await ledger.search("never", None, 20) == SearchPage((), 0)
        assert [event.upstream_id for event in await store.events_after(0)] == ["first", "other"]

    async with opened_search(tmp_path, "demo") as (_, reopened):
        assert (await reopened.search("needle", None, 20)).hits[0].id == first.event.id


async def test_search_bounds_excerpts_around_matches_and_limits_pages(tmp_path: Path) -> None:
    async with opened_search(tmp_path, "demo") as (_, ledger):
        body = "prefix" * 200 + " matchphrase " + "suffix" * 200
        await ledger.append("system", message("first", body))
        latest = await ledger.append("system", message("second", body))
        page = await ledger.search("matchphrase", None, 1)

    assert page.total == 2
    (hit,) = page.hits
    assert hit.id == latest.event.id
    assert "matchphrase" in hit.excerpt
    assert len(hit.excerpt) <= 240
    assert hit.excerpt in body


async def test_index_refuses_a_foreign_transaction_or_project(tmp_path: Path) -> None:
    async with (
        opened_search(tmp_path, "demo") as (store, ledger),
        opened_search(tmp_path, "other") as (other, _),
    ):
        event = (await ledger.append("system", message("first", "synthetic needle"))).event
        with pytest.raises(LedgerError, match="current project"):
            await store.query("other", "needle", None, 20)
        async with other.transaction() as tx:
            with pytest.raises(RuntimeError, match="another project's transaction"):
                await store.index(tx, event)
        assert await other.query("other", "needle", None, 20) == SearchPage((), 0)


async def test_index_uses_the_matching_local_event_body(tmp_path: Path) -> None:
    async with (
        opened_search(tmp_path, "demo") as (store, ledger),
        opened_search(tmp_path, "other") as (_, other),
    ):
        foreign = (await other.append("system", message("first", "foreignneedle"))).event
        async with store.transaction() as tx:
            local = replace(
                foreign, seq=await tx.allocate_seq(), id="evt-local", body="localneedle"
            )
            await tx.insert_event(local)
            assert foreign.seq == local.seq
            with pytest.raises(RuntimeError, match="matching local event"):
                await store.index(tx, foreign)
            await store.index(tx, replace(local, body=foreign.body))

        assert await ledger.search("foreignneedle", None, 20) == SearchPage((), 0)
        assert await ledger.search("localneedle", None, 20) == SearchPage(
            (SearchHit(local.id, local.kind, local.body),), 1
        )


async def test_failed_index_write_rolls_back_ledger_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with opened_search(tmp_path, "demo") as (store, ledger):
        event = message("first", "retryneedle")
        with monkeypatch.context() as patch:
            patch.setattr(
                search_sql, "INSERT_MESSAGE", text("INSERT INTO missing_message_fts VALUES (1)")
            )
            with pytest.raises(OperationalError, match="no such table: missing_message_fts"):
                await ledger.append("system", event)

        assert await store.events_after(0) == []
        assert await store.head_seq() == 0
        assert await ledger.search("retryneedle", None, 20) == SearchPage((), 0)
        retry = await ledger.append("system", event)
        assert retry.created
        assert await ledger.search("retryneedle", None, 20) == SearchPage(
            (SearchHit(retry.event.id, retry.event.kind, retry.event.body),), 1
        )
