import json
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from alembic.script import ScriptDirectory

from conftest import authenticated_client, tool_json
from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore, head_revision, script_heads
from plotroom.adapters.store_sqlite.migrate import MIGRATIONS_DIR, alembic_config, upgrade_to_head
from plotroom.app.config import GitHubConfig
from plotroom.app.serve import Daemon
from plotroom.core.catalog import build_catalog
from plotroom.core.claims import TICKET_TREE_MOVE
from plotroom.core.events import NewEvent
from plotroom.core.github import ObservedPull, apply_pull_poll
from plotroom.core.github_projection import FILES, GitHubRecord, GitHubResource, apply_resource
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork
from test_daemon import bearer, make_config, run_daemon, stop


def test_history_has_exactly_one_head() -> None:
    """CI fails on more than one head: parallel entries must re-parent onto main's head."""
    assert len(script_heads()) == 1


def test_history_is_linear() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    for revision in script.walk_revisions():
        parents = revision.down_revision
        assert parents is None or isinstance(parents, str), f"{revision.revision} is a merge"


async def test_a_new_file_is_migrated_to_head(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    try:
        assert await store.revision() is None
        await store.migrate()
        assert await store.revision() == head_revision()
    finally:
        await store.close()
    assert store.path == tmp_path / "demo.db"
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"project_meta", "alembic_version", "events", "project_seq"} <= tables


async def test_migrating_twice_changes_nothing(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    try:
        await store.migrate()
        await store.migrate()
        assert await store.revision() == head_revision()
    finally:
        await store.close()


async def test_populated_claims_upgrade_reconciles_existing_tree_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening a populated predecessor preserves its rows and derives conflicts immediately."""
    legacy = tmp_path / "predecessor"
    shutil.copytree(
        MIGRATIONS_DIR,
        legacy,
        ignore=shutil.ignore_patterns(
            "__pycache__", "0013_lane_claims.py", "0015_recurring_triggers.py"
        ),
    )
    previous = head_revision(alembic_config(legacy))
    config = make_config(tmp_path)
    config.data_dir.mkdir()
    repo = "fixture/repo"
    stamp = "2026-11-22T12:00:00Z"
    store = SqliteStore("demo", config.data_dir)
    await upgrade_to_head(store._engine, alembic_config(legacy))  # pyright: ignore[reportPrivateUsage]
    assert await store.revision() == previous
    ledger = Ledger(store, build_catalog(), FixedClock())
    pulls = (
        ObservedPull(1, "editor", "a" * 40, repo, "open", stamp, "Existing edit"),
        ObservedPull(2, "mover", "b" * 40, repo, "open", stamp, "Existing move"),
    )
    story = "tickets/story.md"

    async def existing_facts(uow: UnitOfWork) -> None:
        await apply_pull_poll(uow, repo, None, pulls, stamp)
        for pull in pulls:
            renamed = pull.number == 2
            path = "tickets/renamed.md" if renamed else story
            fields = {
                "head_sha": pull.head_sha,
                "number": pull.number,
                "path": path,
                "status": "renamed" if renamed else "modified",
            }
            if renamed:
                fields["previous_path"] = story
            record = GitHubRecord(path, "pull_file", fields)
            await apply_resource(
                uow,
                repo,
                FILES,
                str(pull.number),
                GitHubResource((record,), attributes={"head_sha": pull.head_sha}),
                stamp,
            )

    ledger.start()
    try:
        await ledger.run("system", existing_facts)
        before = await ledger.events_after(0)
        assert len(before) == 4
    finally:
        await ledger.stop()
        await store.close()

    monkeypatch.delenv("PLOTROOM_DEMO_GITHUB_TOKEN", raising=False)
    config = replace(
        config,
        github={"demo": GitHubConfig(repo, None, "http://127.0.0.1:1", 60)},
        github_ticket_trees={"demo": ("tickets", tmp_path)},
    )
    token = bearer(config, "demo")
    observed: list[dict[str, Any]] = []
    for _ in range(2):
        daemon = Daemon(config, clock=FixedClock())
        task, port = await run_daemon(daemon)
        try:
            assert await daemon.ledgers["demo"].events_after(0) == before
            assert (await daemon.ledgers["demo"].pull_request(repo, 1)) is not None
            async with authenticated_client(f"http://127.0.0.1:{port}/mcp", token) as client:
                result = tool_json(await client.call_tool("overlaps", {"pr": 2}))
                (move,) = result["data"]["items"]
                assert move["kind"] == TICKET_TREE_MOVE
                assert move["context"]["suggested_order"] == [1, 2]
                observed.append(result)
        finally:
            await stop(daemon, task)
    assert observed[0] == observed[1]
    disabled = Daemon(replace(config, github={}, github_ticket_trees={}), clock=FixedClock())
    task, port = await run_daemon(disabled)
    try:
        async with authenticated_client(f"http://127.0.0.1:{port}/mcp", token) as client:
            result = tool_json(await client.call_tool("overlaps", {"pr": 2}))
            assert result["data"]["items"] == []
            assert result["data"]["cursor"] > observed[-1]["data"]["cursor"]
    finally:
        await stop(disabled, task)
    assert script_heads() == ["0015"]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0015",)


async def test_recurring_upgrade_keeps_populated_once_trigger_state(tmp_path: Path) -> None:
    legacy = tmp_path / "pre-recurring"
    shutil.copytree(
        MIGRATIONS_DIR,
        legacy,
        ignore=shutil.ignore_patterns("__pycache__", "0015_recurring_triggers.py"),
    )
    prior = alembic_config(legacy)
    assert head_revision(prior) == "0013"
    store = SqliteStore("demo", tmp_path)
    predicate = json.dumps({"pr_merged": {"number": 7}})
    action = json.dumps({"text": "fixture"})
    try:
        await upgrade_to_head(store._engine, prior)  # pyright: ignore[reportPrivateUsage]
        with sqlite3.connect(store.path) as db:
            db.executemany(
                """
                INSERT INTO triggers
                    (id, predicate, action_kind, action_payload, state, bindings,
                     created_seq, fired_seq, action_key)
                VALUES (?, ?, 'notify_orchestrator', ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("open", predicate, action, "open", "{}", 1, None, None),
                    ("fired", predicate, action, "fired", '{"$":7}', 2, 3, "original-key"),
                ],
            )
            db.execute("UPDATE project_seq SET value = 3")
        await store.migrate()
        assert await store.revision() == "0015"
        first, second = await store.list_triggers(None)
        assert (first.mode, first.state, first.was_true) == ("once", "open", False)
        assert (second.mode, second.state, second.fired_seq, second.action_key) == (
            "once",
            "fired",
            3,
            "original-key",
        )
        assert second.bindings == {"$": 7}
    finally:
        await store.close()


async def test_a_file_with_an_unknown_revision_is_not_migrated(tmp_path: Path) -> None:
    path = tmp_path / "demo.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        db.execute("INSERT INTO alembic_version VALUES ('from-the-future')")
    store = SqliteStore("demo", tmp_path)
    try:
        with pytest.raises(Exception, match="from-the-future"):
            await store.migrate()
    finally:
        await store.close()


def test_two_revisions_with_the_same_parent_are_detected(tmp_path: Path) -> None:
    """The check CI relies on: a parallel entry that forgot to re-parent makes a second head."""
    copy = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    head = head_revision()
    (copy / "versions" / "9998_parallel.py").write_text(
        'revision = "parallel"\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n\n'
        "def upgrade() -> None:\n    pass\n",
        encoding="utf-8",
    )
    cfg = alembic_config()
    cfg.set_main_option("script_location", str(copy))
    assert sorted(script_heads(cfg)) == sorted([head, "parallel"])
    with pytest.raises(RuntimeError, match="exactly one head"):
        head_revision(cfg)


async def test_a_data_directory_with_url_special_characters_works(tmp_path: Path) -> None:
    odd = tmp_path / "a?b#c%d"
    odd.mkdir()
    store = SqliteStore("demo", odd)
    try:
        await store.migrate()
        assert await store.revision() == head_revision()
    finally:
        await store.close()
    assert (odd / "demo.db").exists()


def test_an_installation_path_with_a_percent_sign_still_loads_the_history(tmp_path: Path) -> None:
    copy = tmp_path / "100%done" / "migrations"
    shutil.copytree(MIGRATIONS_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert script_heads(alembic_config(copy)) == script_heads()


async def test_a_migration_that_fails_after_its_first_ddl_leaves_nothing_behind(
    tmp_path: Path,
) -> None:
    """DDL and the revision commit together, so the next startup does not trip on a half migration."""
    copy = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    head = head_revision()
    broken = copy / "versions" / "9999_half.py"
    broken.write_text(
        'import sqlalchemy as sa\nfrom alembic import op\n\nrevision = "half"\n'
        f'down_revision = "{head}"\nbranch_labels = None\ndepends_on = None\n\n\n'
        "def upgrade() -> None:\n"
        '    op.create_table("half", sa.Column("id", sa.Integer(), primary_key=True))\n'
        '    raise RuntimeError("stopped after the first DDL statement")\n',
        encoding="utf-8",
    )
    cfg = alembic_config()
    cfg.set_main_option("script_location", str(copy))
    store = SqliteStore("demo", tmp_path)
    try:
        await store.migrate()  # real history: at its head
        with pytest.raises(RuntimeError, match="first DDL"):
            await upgrade_to_head(store._engine, cfg)  # pyright: ignore[reportPrivateUsage]
        assert await store.revision() == head
    finally:
        await store.close()
    with sqlite3.connect(tmp_path / "demo.db") as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "half" not in tables
    # the same revision, repaired, runs from the state the failure left
    broken.write_text(
        'import sqlalchemy as sa\nfrom alembic import op\n\nrevision = "half"\n'
        f'down_revision = "{head}"\nbranch_labels = None\ndepends_on = None\n\n\n'
        "def upgrade() -> None:\n"
        '    op.create_table("half", sa.Column("id", sa.Integer(), primary_key=True))\n',
        encoding="utf-8",
    )
    again = SqliteStore("demo", tmp_path)
    try:
        await upgrade_to_head(again._engine, cfg)  # pyright: ignore[reportPrivateUsage]
        assert await again.revision() == "half"
    finally:
        await again.close()
    with sqlite3.connect(tmp_path / "demo.db") as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "half" in tables


async def test_a_file_at_the_first_revision_is_upgraded_in_place(tmp_path: Path) -> None:
    """The event log arrives by migration: an existing 0001 file gains its tables and counter."""
    first = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, first, ignore=shutil.ignore_patterns("__pycache__"))
    for later in (first / "versions").glob("*.py"):
        if not later.name.startswith("0001_"):
            later.unlink()
    cfg = alembic_config()
    cfg.set_main_option("script_location", str(first))
    store = SqliteStore("demo", tmp_path)
    try:
        await upgrade_to_head(store._engine, cfg)  # pyright: ignore[reportPrivateUsage]
        assert await store.revision() == "0001"
        await store.migrate()
        assert await store.revision() == head_revision()
        assert await store.head_seq() == 0
        assert await store.events_after(0) == []
    finally:
        await store.close()
    with sqlite3.connect(tmp_path / "demo.db") as db:
        assert db.execute("SELECT id, value FROM project_seq").fetchall() == [(1, 0)]


@pytest.mark.parametrize("prior", ["0010", "0011"])
async def test_trigger_migration_preserves_projected_prs_and_pending_actions(
    tmp_path: Path,
    prior: str,
) -> None:
    original = tmp_path / "before-triggers"
    shutil.copytree(MIGRATIONS_DIR, original, ignore=shutil.ignore_patterns("__pycache__"))
    for path in (original / "versions").glob("[0-9]*_*.py"):
        if path.name.split("_", 1)[0] > prior:
            path.unlink()
    config = alembic_config(original)
    repo = "fixture/project"
    store = SqliteStore("demo", tmp_path)
    catalog = replace(build_catalog(github_repository=repo), trigger_leaves=None)
    # The seed already carries the repository. Leave feature reconciliation disabled
    # while writing legacy facts on a schema before the trigger and claim tables.
    ledger = Ledger(store, catalog, FixedClock())
    timestamp = "2026-11-23T12:00:00.000000Z"
    details = GitHubResource((), attributes={"fixture": True}, generation=1, as_of=timestamp)

    async def seed(uow: UnitOfWork) -> str:
        pull = ObservedPull(7, "fixture", "a" * 40, repo, "merged", timestamp, "before upgrade")
        assert await apply_pull_poll(uow, repo, None, (pull,), timestamp)
        if prior == "0011":
            await uow.save_github_resource(repo, "fixture_resource", "", details)
        cause = (await uow.append(NewEvent("core", "core.tick", "before-upgrade"))).event
        return await uow.enqueue_action(cause, "fixture", {"value": "keep"})

    try:
        await upgrade_to_head(store._engine, config)  # pyright: ignore[reportPrivateUsage]
        assert await store.revision() == prior
        ledger.start()
        key = await ledger.run("system", seed)
        await ledger.stop()
        await store.migrate()
        assert await store.revision() == head_revision()
        projected = await store.pull_request(repo, 7)
        assert projected is not None
        assert projected.state == "merged"
        if prior == "0011":
            assert await store.github_resource(repo, "fixture_resource") == details
        action = await store.pending_action()
        assert action is not None
        assert (action.key, action.payload) == (key, {"value": "keep"})
        assert not await store.list_triggers(None)
        async with store.transaction() as tx:
            assert await tx.first_matching_pull(repo, "fixture*") == 7
    finally:
        await ledger.stop()
        await store.close()
