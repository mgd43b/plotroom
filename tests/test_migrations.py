import json
import shutil
import sqlite3
from contextlib import closing
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
from plotroom.core.github import PR_MERGED, PR_OPENED, ObservedPull
from plotroom.core.github_projection import FILES, GitHubRecord, GitHubResource
from plotroom.core.lanes import Lane
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork
from test_daemon import bearer, make_config, run_daemon, stop


def _migrations_at(destination: Path, revision: str) -> Path:
    shutil.copytree(MIGRATIONS_DIR, destination, ignore=shutil.ignore_patterns("__pycache__"))
    script = ScriptDirectory.from_config(alembic_config(destination))
    keep = {item.revision for item in script.walk_revisions(head=revision)}
    for path in (destination / "versions").glob("[0-9]*_*.py"):
        if path.name.split("_", 1)[0] not in keep:
            path.unlink()
    assert head_revision(alembic_config(destination)) == revision
    return destination


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


def seed_legacy_pull(path: Path, repository: str, pull: ObservedPull) -> None:
    # Keep the fixture at its historical schema when current writers gain columns.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "INSERT INTO github_pull_requests (repository, number, branch, head_sha, "
            "head_repository, state, updated_at, as_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                repository,
                pull.number,
                pull.branch,
                pull.head_sha,
                pull.head_repository,
                pull.state,
                pull.updated_at,
                pull.updated_at,
            ),
        )


async def append_legacy_pull(uow: UnitOfWork, repository: str, pull: ObservedPull) -> None:
    fields = {
        "repository": repository,
        "number": pull.number,
        "branch": pull.branch,
        "head_sha": pull.head_sha,
        "state": pull.state,
        "updated_at": pull.updated_at,
    }
    if pull.head_repository is not None:
        fields["head_repository"] = pull.head_repository
    await uow.append(
        NewEvent(
            "github",
            PR_MERGED if pull.state == "merged" else PR_OPENED,
            f"{repository}#{pull.number}@1:{pull.updated_at}",
            fields,
            pull.body,
        )
    )


async def test_checklist_upgrade_preserves_existing_ready_lane(tmp_path: Path) -> None:
    revision = ScriptDirectory.from_config(alembic_config()).get_revision("0017")
    assert revision is not None
    predecessor = revision.down_revision
    assert isinstance(predecessor, str)
    prior = alembic_config(_migrations_at(tmp_path / "before-checklists", predecessor))
    store = SqliteStore("demo", tmp_path)
    lane = Lane(
        "existing", "fixture", ("ticket.1",), "fixture-branch", "ready", pr=7, head_sha="abc"
    )
    try:
        await upgrade_to_head(store._engine, prior)  # pyright: ignore[reportPrivateUsage]
        async with store.transaction() as tx:
            await tx.save_lane(lane)
        await store.migrate()
        assert await store.revision() == head_revision()
        board, *_ = await store.read_snapshot("orchestrator", None, checklist_lane=lane.id)
        assert board[3] == [lane]
        assert "checklist" not in board[3][0].render()
    finally:
        await store.close()


async def test_populated_claims_upgrade_reconciles_existing_tree_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening a populated predecessor preserves its rows and derives conflicts immediately."""
    previous = "0012"
    legacy = _migrations_at(tmp_path / "predecessor", previous)
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
    for pull in pulls:
        seed_legacy_pull(store.path, repo, pull)

    async def existing_facts(uow: UnitOfWork) -> None:
        for pull in pulls:
            await append_legacy_pull(uow, repo, pull)
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
            await uow.append(
                NewEvent(
                    "github",
                    "github.pull_file",
                    f"{repo}:files:{pull.number}:{path}@1",
                    {
                        "repository": repo,
                        **fields,
                    },
                )
            )
            await uow.save_github_resource(
                repo,
                FILES,
                str(pull.number),
                GitHubResource(
                    (record,), attributes={"head_sha": pull.head_sha}, generation=1, as_of=stamp
                ),
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
    assert script_heads() == [head_revision()]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            head_revision(),
        )


async def test_recurring_upgrade_keeps_populated_once_trigger_state(tmp_path: Path) -> None:
    legacy = _migrations_at(tmp_path / "pre-recurring", "0013")
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
        assert await store.revision() == head_revision()
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
    cfg = alembic_config(_migrations_at(tmp_path / "migrations", "0001"))
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
    config = alembic_config(_migrations_at(tmp_path / "before-triggers", prior))
    repo = "fixture/project"
    store = SqliteStore("demo", tmp_path)
    catalog = replace(build_catalog(github_repository=repo), trigger_leaves=None)
    # The seed already carries the repository. Leave feature reconciliation disabled
    # while writing legacy facts on a schema before the trigger and claim tables.
    ledger = Ledger(store, catalog, FixedClock())
    timestamp = "2026-11-23T12:00:00.000000Z"
    details = GitHubResource((), attributes={"fixture": True}, generation=1, as_of=timestamp)
    pull = ObservedPull(7, "fixture", "a" * 40, repo, "merged", timestamp, "before upgrade")

    async def seed(uow: UnitOfWork) -> str:
        await append_legacy_pull(uow, repo, pull)
        if prior == "0011":
            await uow.save_github_resource(repo, "fixture_resource", "", details)
        cause = (await uow.append(NewEvent("core", "core.tick", "before-upgrade"))).event
        return await uow.enqueue_action(cause, "fixture", {"value": "keep"})

    try:
        await upgrade_to_head(store._engine, config)  # pyright: ignore[reportPrivateUsage]
        assert await store.revision() == prior
        seed_legacy_pull(store.path, repo, pull)
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


async def test_merge_controller_upgrade_preserves_existing_lane_and_pull(tmp_path: Path) -> None:
    legacy = _migrations_at(tmp_path / "before-merge-controller", "0015")
    repository = "fixture/project"
    timestamp = "2026-11-23T12:00:00.000000Z"
    head = "a" * 40
    store = SqliteStore("demo", tmp_path)
    try:
        await upgrade_to_head(store._engine, alembic_config(legacy))  # pyright: ignore[reportPrivateUsage]
        assert await store.revision() == head_revision(alembic_config(legacy))
        seed_legacy_pull(
            store.path, repository, ObservedPull(7, "work", head, repository, "open", timestamp, "")
        )
        with closing(sqlite3.connect(store.path)) as db, db:
            db.execute(
                "INSERT INTO lanes (id, worker, work_items, branch, state, pr, head_sha) "
                "VALUES ('lane', 'worker', '[\"ticket\"]', 'work', 'ready', 7, ?)",
                (head,),
            )
        await store.migrate()
        assert await store.revision() == head_revision()
        snapshot = await store.read_merge_gate(repository, 7)
        assert snapshot.pull is not None
        assert snapshot.pull.draft is None
        assert snapshot.pull.merge_commit_sha is None
        assert snapshot.lane is not None
        assert snapshot.lane.branch_pattern is None
        assert (snapshot.lane.state, snapshot.lane.pr, snapshot.lane.head_sha) == ("ready", 7, head)
        assert snapshot.lane.work_items == ("ticket",)
        assert snapshot.earlier_pull is None
    finally:
        await store.close()
