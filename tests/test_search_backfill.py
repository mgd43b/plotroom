"""Search migrations and restarting a bounded backfill over retained sources."""

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Generator
from contextlib import closing, contextmanager
from dataclasses import replace
from functools import partial
from pathlib import Path

import pytest
from sqlalchemy import URL, create_engine, event
from sqlalchemy.engine import Connection

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore, head_revision, script_heads
from plotroom.adapters.store_sqlite.migrate import alembic_config, upgrade_to_head
from plotroom.adapters.store_sqlite.store import retryable_search_write_error
from plotroom.core import search
from plotroom.core.ask_checks import Ask
from plotroom.core.catalog import build_catalog
from plotroom.core.events import Event, NewEvent
from plotroom.core.github import COMMENTS, ObservedPull
from plotroom.core.github_projection import PULL_TEXT, GitHubResource, apply_resource
from plotroom.core.human_queue import Decision
from plotroom.core.intake import SESSION_MESSAGE, TURN_REPORT
from plotroom.core.ledger import Ledger
from plotroom.core.merge_flakes import FLAKE_EVENT
from plotroom.core.search import backfill_search
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.search import (
    ASK_KIND,
    DECISION_KIND,
    ISSUE_COMMENT_KIND,
    MESSAGE_KIND,
    PR_BODY_KIND,
    BackfillPage,
    BackfillSource,
    SearchHit,
    SearchPage,
    SearchRecord,
)
from test_migrations import _migrations_at  # pyright: ignore[reportPrivateUsage]
from test_search import message, opened_search
from test_search_sources import (
    COMMENT_ID,
    LATER,
    LATEST,
    PULL_ID,
    REPO,
    START,
    comment,
    indexed_rows,
    project_comment,
    project_pull,
)


async def _seed_snapshots(uow: UnitOfWork) -> None:
    await uow.insert_decision(
        Decision("dec_historical", "human", "needle decision", {}, [], [], START)
    )
    await uow.save_ask(Ask("ask_historical", REPO, 2, "needle ask", ("A", "B"), 93, START, START))
    await uow.save_github_resource(REPO, COMMENTS, "00", GitHubResource((), complete=False))
    await project_comment(uow, comment("superseded comment"))
    await project_comment(uow, comment("needle current comment", LATER))

    head = "a" * 40
    pull = ObservedPull(7, "pull", head, REPO, "open", START, "superseded open body")
    await project_pull(uow, pull)
    await project_pull(uow, replace(pull, updated_at=LATER))
    await apply_resource(
        uow,
        REPO,
        PULL_TEXT,
        "7",
        GitHubResource(
            (),
            attributes={
                "head_sha": head,
                "pull_version": 1,
                "pull_updated_at": LATER,
                "body": "needle retained body edit",
            },
        ),
        LATER,
    )
    await project_pull(uow, replace(pull, updated_at=LATEST))
    await apply_resource(uow, REPO, PULL_TEXT, "7", GitHubResource((), complete=False), LATEST)
    retained = await uow.github_resource(REPO, PULL_TEXT, "7")
    assert retained is not None
    assert not retained.complete
    assert retained.attributes["body"] == "needle retained body edit"

    fallback = replace(pull, number=9)
    await project_pull(uow, fallback)
    await project_pull(
        uow, replace(fallback, updated_at=LATER, state="closed", body="needle closed body")
    )
    await project_pull(
        uow, replace(fallback, updated_at=LATER, state="closed", body="unretained list body")
    )
    await uow.save_github_resource(
        REPO,
        PULL_TEXT,
        "9",
        GitHubResource(
            (),
            attributes={
                "head_sha": head,
                "pull_version": 1,
                "pull_updated_at": START,
                "body": "superseded snapshot",
            },
        ),
    )
    same_body = replace(pull, number=10, body="needle unchanged body")
    await project_pull(uow, same_body)
    await project_pull(uow, replace(same_body, updated_at=LATEST))


def _pull_provenance(path: Path) -> dict[str, tuple[int, bool]]:
    with closing(sqlite3.connect(path)) as db:
        return {
            record_id: (seq, bool(provisional))
            for record_id, seq, provisional in db.execute(
                "SELECT record_id, seq, provisional FROM search_records WHERE kind = ?",
                (PR_BODY_KIND,),
            )
        }


async def test_populated_upgrade_stops_and_restarts_from_exact_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = alembic_config(_migrations_at(tmp_path / "before-backfill", "0018"))
    old_store = SqliteStore("demo", tmp_path)
    old = Ledger(old_store, build_catalog(frozenset({"fixture"})), FixedClock())
    expected: dict[str, str] = {}
    try:
        await upgrade_to_head(old_store._engine, prior)  # pyright: ignore[reportPrivateUsage]
        assert await old_store.revision() == "0018"
        old.start()
        oldest = (await old.append("system", message("oldest", "needle earliest"))).event
        ignored = (
            await old.append(
                "system", NewEvent("core", "core.tick", "ignore", {}, "needle ignored")
            )
        ).event
        preindexed = (await old.append("system", message("preindexed", "needle existing"))).event
        expected.update({oldest.id: MESSAGE_KIND, preindexed.id: MESSAGE_KIND})
        for source in (
            NewEvent("claude", SESSION_MESSAGE, "s", {"session_id": "fixture"}, "needle chat"),
            NewEvent(
                "claude",
                TURN_REPORT,
                "r",
                {"session_id": "fixture", "report_digest": "fixture-digest"},
                "needle report",
            ),
            NewEvent(
                "core",
                FLAKE_EVENT,
                "f",
                {
                    "repository": REPO,
                    "head_sha": "a" * 40,
                    "check_id": 8,
                    "check_name": "needle check",
                    "pattern": "same head",
                },
            ),
        ):
            event = (await old.append("system", source)).event
            expected[event.id] = event.kind
        await old.run("system", _seed_snapshots)
        await old.execute(
            "orchestrator",
            "lane_start",
            "lane",
            {
                "lane": "fixture",
                "worker": "fixture",
                "branch": "work",
                "work_items": [],
            },
        )
        budget = await old.execute(
            "orchestrator",
            "budget_open",
            "budget",
            {
                "run": "fixture-run",
                "lane": "fixture",
                "cap_usd": 10,
                "provider": "fixture",
                "key_scope": "staging",
            },
        )
        await old.execute(
            "orchestrator", "route", "route", {"action_kind": "build", "lane": "fixture"}
        )
        with closing(sqlite3.connect(old_store.path)) as db, db:
            db.execute(
                "INSERT INTO message_fts (rowid, body) VALUES (?, ?)",
                (preindexed.seq, preindexed.body),
            )
    finally:
        await old.stop()
        await old_store.close()

    expected.update(
        {
            "dec_historical": DECISION_KIND,
            "ask_historical": ASK_KIND,
            COMMENT_ID: ISSUE_COMMENT_KIND,
            PULL_ID: PR_BODY_KIND,
            f"{REPO}#9": PR_BODY_KIND,
            f"{REPO}#10": PR_BODY_KIND,
        }
    )

    async with opened_search(tmp_path, "demo") as (store, ledger):
        assert await store.revision() == head_revision()
        assert script_heads() == [head_revision()]
        assert await ledger.budget_status("orchestrator") == [budget.result]
        with closing(sqlite3.connect(store.path)) as db:
            assert db.execute("SELECT action_kind, lane FROM routes").fetchall() == [
                ("build", "fixture")
            ]
        assert await ledger.search("needle", None, 20) == SearchPage(
            (SearchHit(preindexed.id, MESSAGE_KIND, preindexed.body),), 1
        )
        read = ledger.events_after
        positions: list[int] = []

        class Interrupted(Exception):
            pass

        async def interrupt(seq: int, limit: int) -> list[Event]:
            positions.append(seq)
            if seq == ignored.seq:
                raise Interrupted
            return await read(seq, limit)

        monkeypatch.setattr(ledger, "events_after", interrupt)
        with pytest.raises(Interrupted):
            await backfill_search(ledger, store, limit=1)
        assert positions == [0, oldest.seq, ignored.seq]
        assert await store.backfill_position(BackfillSource.EVENTS) == (ignored.seq,)
        assert await store.backfill_position(BackfillSource.DECISIONS) == ()
        assert (await ledger.search("earliest", None, 20)).total == 1

    async with (
        opened_search(tmp_path, "demo") as (store, ledger),
        opened_search(tmp_path, "other") as (other_store, other),
    ):
        read = ledger.events_after
        positions.clear()

        async def observe(seq: int, limit: int) -> list[Event]:
            positions.append(seq)
            return await read(seq, limit)

        monkeypatch.setattr(ledger, "events_after", observe)
        await asyncio.wait_for(backfill_search(ledger, store, limit=1), timeout=10)
        assert positions[0] == ignored.seq
        page = await ledger.search("needle", None, 20, role="orchestrator")
        assert page.total == len(expected)
        assert {hit.id: hit.kind for hit in page.hits} == expected
        assert await ledger.search("superseded", None, 20) == SearchPage((), 0)
        assert (await ledger.search("retained body edit", (PR_BODY_KIND,), 20)).hits[
            0
        ].id == PULL_ID
        assert (await ledger.search("closed body", (PR_BODY_KIND,), 20)).hits[0].id == f"{REPO}#9"
        for source in BackfillSource:
            assert await store.backfill_position(source) is None
        await backfill_search(other, other_store, limit=1)
        assert await other.search("needle", None, 20, role="orchestrator") == SearchPage((), 0)
        before = indexed_rows(store.path)
        positions.clear()
        await backfill_search(ledger, store, limit=1)
        assert positions == []
        assert indexed_rows(store.path) == before
        provenance = _pull_provenance(store.path)
        assert provenance[PULL_ID][1]
        assert provenance[f"{REPO}#9"][1]
        assert provenance[f"{REPO}#10"][1]

        async def confirm_details(uow: UnitOfWork) -> None:
            for number, body in (
                (7, "verified snapshot body"),
                (9, "verified current body"),
                (10, "needle unchanged body"),
            ):
                pull = await uow.pull_request(REPO, number)
                assert pull is not None
                await apply_resource(
                    uow,
                    REPO,
                    PULL_TEXT,
                    str(number),
                    GitHubResource(
                        (),
                        attributes={
                            "head_sha": pull.head_sha,
                            "pull_version": pull.version,
                            "pull_updated_at": pull.updated_at,
                            "body": body,
                        },
                    ),
                    LATEST,
                )

        await ledger.run("system", confirm_details)
        assert await ledger.search("retained body edit", (PR_BODY_KIND,), 20) == SearchPage((), 0)
        assert await ledger.search("verified snapshot", (PR_BODY_KIND,), 20) == SearchPage(
            (SearchHit(PULL_ID, PR_BODY_KIND, "verified snapshot body"),), 1
        )
        assert await ledger.search("closed body", (PR_BODY_KIND,), 20) == SearchPage((), 0)
        assert await ledger.search("verified current", (PR_BODY_KIND,), 20) == SearchPage(
            (SearchHit(f"{REPO}#9", PR_BODY_KIND, "verified current body"),), 1
        )
        confirmed = _pull_provenance(store.path)
        assert confirmed[PULL_ID][0] > provenance[PULL_ID][0]
        assert not confirmed[PULL_ID][1]
        assert confirmed[f"{REPO}#9"][0] > provenance[f"{REPO}#9"][0]
        assert not confirmed[f"{REPO}#9"][1]
        assert confirmed[f"{REPO}#10"] == (provenance[f"{REPO}#10"][0], False)


async def test_failed_page_rolls_back_insert_and_cursor_then_retries(tmp_path: Path) -> None:
    async with opened_search(tmp_path, "demo") as (store, ledger):
        source = BackfillSource.DECISIONS
        first = SearchRecord(DECISION_KIND, "a", "rollback first")
        second = SearchRecord(DECISION_KIND, "b", "rollback second")
        page = BackfillPage(("b",), (first, second))

        async def fail_after_checkpoint(uow: UnitOfWork) -> None:
            assert await uow.save_search_backfill(store, source, (), page)
            assert await store.backfill_position(source) == ()
            raise ValueError("interrupted transaction")

        with pytest.raises(ValueError, match="interrupted transaction"):
            await ledger.run("system", fail_after_checkpoint)
        assert await store.backfill_position(source) == ()
        assert indexed_rows(store.path) == {}
        commit = partial(
            UnitOfWork.save_search_backfill, index=store, source=source, expected=(), page=page
        )
        assert await ledger.run("system", commit)
        assert await store.backfill_position(source) == ("b",)
        before = indexed_rows(store.path)
        assert not await ledger.run("system", commit)
        assert indexed_rows(store.path) == before
        assert len(before) == 2


@contextmanager
def contend_after_checkpoint(
    store: SqliteStore,
    fault_path: Path,
    checkpoint: Callable[[str, tuple[object, ...]], bool],
    on_write: Callable[[], bool],
    *,
    at_commit: bool = False,
    full: bool = False,
) -> Generator[None]:
    """Inject a real SQLite BUSY or FULL after checkpoint SQL or at commit."""
    with closing(sqlite3.connect(fault_path, isolation_level=None)) as blocker:
        blocker.execute("CREATE TABLE held (value BLOB)")
        if not full:
            blocker.execute("BEGIN IMMEDIATE")
        fault_engine = create_engine(
            URL.create("sqlite", database=str(fault_path)), connect_args={"timeout": 0}
        )
        engine = store._engine.sync_engine  # pyright: ignore[reportPrivateUsage]
        pending: Connection | None = None

        def fail_write() -> None:
            with fault_engine.connect() as connection:
                if full:
                    pages = connection.exec_driver_sql("PRAGMA page_count").scalar_one()
                    connection.exec_driver_sql(f"PRAGMA max_page_count={pages}")
                    connection.exec_driver_sql("INSERT INTO held VALUES (zeroblob(1048576))")
                    return
                connection.exec_driver_sql("INSERT INTO held VALUES (1)")

        def after_write(
            conn: Connection,
            _cursor: object,
            sql: str,
            parameters: tuple[object, ...],
            _context: object,
            _executemany: bool,
        ) -> None:
            nonlocal pending
            if checkpoint(sql, parameters) and on_write():
                if at_commit:
                    pending = conn
                else:
                    fail_write()

        def before_commit(conn: Connection) -> None:
            nonlocal pending
            if conn is pending:
                pending = None
                fail_write()

        event.listen(engine, "after_cursor_execute", after_write)
        if at_commit:
            event.listen(engine, "commit", before_commit)
        try:
            yield
        finally:
            event.remove(engine, "after_cursor_execute", after_write)
            if at_commit:
                event.remove(engine, "commit", before_commit)
            fault_engine.dispose()


@pytest.mark.parametrize("outcome", ["recover", "exhaust", "cancel"])
async def test_backfill_write_contention_uses_the_committed_checkpoint(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    caplog.set_level(logging.INFO, logger="plotroom.search")
    source = BackfillSource.DECISIONS
    delays = (60.0,) * 3 if outcome == "cancel" else (0.0,) * 3
    monkeypatch.setattr(search, "_WRITE_RETRY_DELAYS", delays)
    async with opened_search(tmp_path, "demo") as (store, ledger):
        # Retained decisions with no search rows simulate a populated database before backfill.
        async with store.transaction() as tx:
            for decision_id in ("a", "b", "c", "d"):
                await tx.insert_decision(
                    Decision(decision_id, "human", f"needle {decision_id}", {}, [], [], START)
                )
        failed = asyncio.Event()
        page_attempts: list[set[tuple[str, str]]] = []

        def observe() -> bool:
            with closing(sqlite3.connect(store.path)) as db:
                checkpoint = db.execute(
                    "SELECT value FROM project_meta WHERE key = ?", (f"search.backfill.{source}",)
                ).fetchone()
                assert checkpoint == ('["b"]',)
            page_attempts.append(set(indexed_rows(store.path)))
            failed.set()
            return outcome != "recover" or len(page_attempts) == 1

        with contend_after_checkpoint(
            store,
            tmp_path / "contended.db",
            lambda sql, parameters: (
                "INSERT INTO project_meta (key, value)" in sql
                and parameters == (f"search.backfill.{source}", '["d"]')
            ),
            observe,
        ):
            task = asyncio.create_task(
                backfill_search(
                    ledger, store, limit=2, retryable_write_error=retryable_search_write_error
                )
            )
            if outcome == "cancel":
                await asyncio.wait_for(failed.wait(), 5)
                # The writer can service foreground work during the backoff.
                await ledger.append("system", message("foreground", "foreground only"))
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await asyncio.wait_for(task, 5)

        if outcome == "recover":
            assert await store.backfill_position(source) is None
            assert page_attempts == [{(DECISION_KIND, "a"), (DECISION_KIND, "b")}] * 2
            expected = [("search write will retry", 1), ("search write recovered", 2)]
        else:
            assert await store.backfill_position(source) == ("b",)
            assert (await ledger.search("needle", None, 20, role="orchestrator")).total == 2
            attempts = 4 if outcome == "exhaust" else 1
            assert page_attempts == [{(DECISION_KIND, "a"), (DECISION_KIND, "b")}] * attempts
            expected = [("search write will retry", 1)]
            if outcome == "exhaust":
                expected += [
                    ("search write will retry", 2),
                    ("search write will retry", 3),
                    ("search write stopped", 4),
                ]
            await backfill_search(
                ledger, store, limit=2, retryable_write_error=retryable_search_write_error
            )
            assert await store.backfill_position(source) is None
        page = await ledger.search("needle", None, 20, role="orchestrator")
        assert page.total == 4
        assert {hit.id for hit in page.hits} == {"a", "b", "c", "d"}
        records = [record for record in caplog.records if record.name == "plotroom.search"]
        assert [(record.getMessage(), record.__dict__["attempt"]) for record in records] == expected
        for record in records:
            assert record.__dict__["project"] == "demo"
            assert record.__dict__["source"] == "backfill.decisions"
            assert record.__dict__["failure_code"] == "sqlite_busy"
            assert record.exc_info is None


async def test_permanent_search_write_failure_is_logged_once_without_sql_details(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async with opened_search(tmp_path, "demo") as (store, ledger):
        with closing(sqlite3.connect(store.path)) as db, db:
            db.execute("""
                CREATE TRIGGER fail_backfill BEFORE INSERT ON project_meta
                WHEN NEW.key = 'search.backfill.events'
                BEGIN SELECT RAISE(ABORT, 'private fixture detail'); END
            """)
        await backfill_search(ledger, store, retryable_write_error=retryable_search_write_error)
        assert await store.backfill_position(BackfillSource.EVENTS) == ()
        records = [record for record in caplog.records if record.name == "plotroom.search"]
        assert len(records) == 1
        (record,) = records
        assert record.getMessage() == "search write stopped"
        assert record.__dict__["source"] == "backfill.events"
        assert record.__dict__["attempt"] == 1
        assert record.__dict__["failure_code"] == "search_write_failed"
        assert record.exc_info is None
        assert "private fixture detail" not in str(record.__dict__)


async def test_projection_edit_while_page_is_in_flight_preserves_live_text(tmp_path: Path) -> None:
    async with opened_search(tmp_path, "demo") as (store, ledger):
        async with store.transaction() as tx:
            await tx.save_github_resource(
                REPO, COMMENTS, "42", GitHubResource((comment("stale body"),))
            )
        source = BackfillSource.COMMENTS
        page = await store.backfill_page(source, (), 1)
        assert page.records == (SearchRecord(ISSUE_COMMENT_KIND, COMMENT_ID, "stale body"),)
        await ledger.run("system", partial(project_comment, row=comment("new body", LATER)))
        assert await ledger.run(
            "system",
            partial(
                UnitOfWork.save_search_backfill, index=store, source=source, expected=(), page=page
            ),
        )
        assert await store.backfill_position(source) == (REPO, "42")
        assert await ledger.search("stale", None, 20) == SearchPage((), 0)
        assert await ledger.search("new", None, 20) == SearchPage(
            (SearchHit(COMMENT_ID, ISSUE_COMMENT_KIND, "new body"),), 1
        )
