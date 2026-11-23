import asyncio
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from conftest import start_plotroom, tool_json, write_config
from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.tokens import TokenRegistry
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError
from plotroom.core.github import PR_MERGED, PULL_REQUESTS, ObservedPull, apply_pull_poll
from plotroom.core.ledger import Ledger
from test_github import HEAD, REPO, TOKEN, FixtureGitHub, pull
from test_github import github_fixture as github_fixture
from test_lanes import lane_client

BRANCH = "fixture-branch"
STEPS = [
    {"step": "Verify checks", "owner": "fixture", "ref": "ci://checks"},
    {"step": "Verify merge", "owner": "fixture", "ref": "git://merge"},
    {"step": "Record evidence", "owner": "fixture", "ref": "ticket://evidence"},
]


@pytest.fixture
async def board(tmp_path: Path) -> AsyncIterator[tuple[Ledger, SqliteStore]]:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    workers = frozenset({"fixture", "peer"})
    ledger = Ledger(
        store, build_catalog(workers, github_repository=REPO), FixedClock(), github_repository=REPO
    )
    ledger.start()
    try:
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "start",
            {"lane": "lane", "worker": "fixture", "branch": BRANCH, "work_items": []},
        )
        yield ledger, store
    finally:
        await ledger.stop()
        await store.close()


def _pull() -> ObservedPull:
    return ObservedPull(12, BRANCH, HEAD, REPO, "open", "2026-11-22T12:00:01Z", "Fixture")


async def _poll(ledger: Ledger, *pulls: ObservedPull, repository: str = REPO) -> None:
    baseline = await ledger.fetch_cursor(repository, PULL_REQUESTS)
    assert await ledger.run(
        "system",
        lambda uow: apply_pull_poll(uow, repository, baseline, pulls, "2026-11-22T12:00:20Z"),
    )


def _ready(checklist: Any = STEPS, *, head: str = HEAD) -> dict[str, Any]:
    return {"lane": "lane", "pr": 12, "head_sha": head, "checklist": checklist}


async def _lane(ledger: Ledger) -> dict[str, Any]:
    board, *_ = await ledger.snapshot_summary("orchestrator", checklist_lane="lane")
    return board[3][0].render()


async def test_ready_stores_three_ordered_rows_and_activates_once(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, store = board
    observed = _pull()
    await _poll(ledger, observed)
    result = await ledger.execute("worker:fixture", "lane_ready", "first", _ready())
    assert result.result["state"] == "ready"
    assert (await ledger.execute("worker:fixture", "lane_ready", "first", _ready())).replayed
    await ledger.execute("worker:fixture", "lane_ready", "again", _ready())
    pending = (await _lane(ledger))["checklist"]
    assert pending == {
        "version": 1,
        "state": "pending",
        "pr": observed.number,
        "head_sha": observed.head_sha,
        "steps": [{**step, "completion_ref": None} for step in STEPS],
    }
    with sqlite3.connect(store.path) as read:
        assert read.execute(
            "SELECT step, owner, ref FROM lane_checklist_steps ORDER BY position"
        ).fetchall() == [(step["step"], step["owner"], step["ref"]) for step in STEPS]

    await _poll(ledger)  # A missing observation neither clears nor activates the current READY.
    assert (await _lane(ledger))["checklist"] == pending
    merged = replace(observed, state="merged", updated_at="2026-11-22T12:00:02Z")
    await _poll(ledger, merged)
    await _poll(ledger, merged)
    lane = await _lane(ledger)
    assert lane["state"] == "active"
    assert (lane["pr"], lane["head_sha"]) == (observed.number, observed.head_sha)
    active = {**pending, "state": "active"}
    assert lane["checklist"] == active
    assert len([event for event in await ledger.events_after(0) if event.kind == PR_MERGED]) == 1

    later = replace(observed, number=13, head_sha="b" * 40, updated_at="2026-11-22T12:00:03Z")
    await _poll(ledger, later)
    assert (await ledger.snapshot_board("orchestrator"))[0][3][0].pr == later.number
    with pytest.raises(LedgerError, match="already has an active checklist"):
        await ledger.execute(
            "orchestrator",
            "lane_ready",
            "replace-active",
            {**_ready([], head=later.head_sha), "pr": later.number},
        )
    with pytest.raises(LedgerError, match="already has an active checklist"):
        await ledger.execute(
            "orchestrator",
            "lane_ready",
            "replace-with-legacy-ready",
            {"lane": "lane", "head_sha": later.head_sha, "pr": later.number},
        )
    lane = await _lane(ledger)
    assert (lane["pr"], lane["head_sha"]) == (observed.number, observed.head_sha)
    assert lane["checklist"] == active


async def test_wrong_or_stale_merge_cannot_activate_pending_ready(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, _ = board
    observed = _pull()
    await _poll(ledger, observed)
    await ledger.execute("orchestrator", "lane_ready", "original", _ready())
    pending = (await _lane(ledger))["checklist"]
    await _poll(ledger, replace(observed, state="merged", updated_at="2026-11-22T12:00:00Z"))
    await _poll(ledger, replace(observed, state="merged", number=13))
    other_repo = "fixture/other"
    await _poll(ledger, replace(observed, head_repository=other_repo), repository=other_repo)
    await _poll(
        ledger, replace(observed, state="merged", head_repository=other_repo), repository=other_repo
    )
    assert (await _lane(ledger))["checklist"] == pending

    await _poll(ledger, replace(observed, state="closed", updated_at="2026-11-22T12:00:02Z"))
    assert "checklist" not in await _lane(ledger)
    reopened = replace(observed, updated_at="2026-11-22T12:00:03Z")
    await _poll(ledger, reopened)
    await ledger.execute("orchestrator", "lane_ready", "original", _ready())  # Receipt only.
    assert "checklist" not in await _lane(ledger)
    await ledger.execute("orchestrator", "lane_ready", "reopened", _ready())
    assert (await _lane(ledger))["checklist"]["version"] == 2


async def test_explicit_ready_activates_when_multiple_lanes_use_the_branch(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, _ = board
    observed = _pull()
    await _poll(ledger, observed)
    await ledger.execute("worker:fixture", "lane_ready", "ready", _ready())
    await ledger.execute(
        "orchestrator",
        "lane_start",
        "start-peer",
        {"lane": "peer", "worker": "peer", "branch": BRANCH, "work_items": []},
    )
    await _poll(ledger, replace(observed, state="merged", updated_at="2026-11-22T12:00:02Z"))
    assert (await _lane(ledger))["checklist"]["state"] == "active"
    lanes = (await ledger.read_board("orchestrator"))[3]
    assert lanes[1].id == "peer"
    assert lanes[1].checklist is None


async def test_explicit_ready_follows_its_pull_when_another_opens_on_the_branch(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, _ = board
    observed = _pull()
    await _poll(ledger, observed)
    await ledger.execute("worker:fixture", "lane_ready", "ready", _ready())
    pending = await _lane(ledger)

    other = replace(observed, number=13, head_sha="b" * 40, updated_at="2026-11-22T12:00:02Z")
    await _poll(ledger, other)
    summary, _ = await ledger.snapshot_board("orchestrator")
    assert summary[2] == {"ready": 1}
    (row,) = summary[3]
    assert (row.state, row.pr, row.head_sha) == ("ready", observed.number, observed.head_sha)
    assert "checklist" not in row.render()
    assert await _lane(ledger) == pending

    await _poll(ledger, replace(observed, state="merged", updated_at="2026-11-22T12:00:03Z"))
    active = await _lane(ledger)
    assert active == {
        **pending,
        "state": "active",
        "checklist": {**pending["checklist"], "state": "active"},
    }
    summary, _ = await ledger.snapshot_board("orchestrator")
    (row,) = summary[3]
    assert (row.state, row.pr, row.head_sha) == ("active", other.number, other.head_sha)


async def test_omitted_ready_only_retains_checklist_for_the_same_repository(
    board: tuple[Ledger, SqliteStore],
) -> None:
    ledger, store = board
    observed = _pull()
    await _poll(ledger, observed)
    await ledger.execute("worker:fixture", "lane_ready", "typed", _ready())
    pending = await _lane(ledger)
    legacy = {"lane": "lane", "pr": observed.number, "head_sha": observed.head_sha}
    await ledger.execute("worker:fixture", "lane_ready", "same-repository", legacy)
    assert await _lane(ledger) == pending

    await ledger.stop()
    other_repository = "fixture/other"
    reconfigured = Ledger(
        store,
        build_catalog(frozenset({"fixture"}), github_repository=other_repository),
        FixedClock(),
        github_repository=other_repository,
    )
    reconfigured.start()
    try:
        await _poll(
            reconfigured,
            replace(observed, head_repository=other_repository),
            repository=other_repository,
        )
        await reconfigured.execute("worker:fixture", "lane_ready", "new-repository", legacy)
        assert "checklist" not in await _lane(reconfigured)
        await _poll(
            reconfigured,
            replace(observed, state="merged", updated_at="2026-11-22T12:00:02Z"),
        )
        assert "checklist" not in await _lane(reconfigured)
    finally:
        await reconfigured.stop()


@pytest.mark.parametrize("changed", ["head_sha", "branch", "head_repository"])
async def test_merge_with_changed_identity_invalidates_pending_ready(
    board: tuple[Ledger, SqliteStore], changed: str
) -> None:
    ledger, _ = board
    observed = _pull()
    await _poll(ledger, observed)
    await ledger.execute("orchestrator", "lane_ready", "original", _ready())
    wrong_identity = {"head_sha": "b" * 40, "branch": "other", "head_repository": "fork/repo"}
    await _poll(
        ledger,
        replace(
            observed,
            state="merged",
            updated_at="2026-11-22T12:00:02Z",
            **{changed: wrong_identity[changed]},
        ),
    )
    assert "checklist" not in await _lane(ledger)


@pytest.mark.parametrize(
    "invalid", ["free text", ["free text"], [{"step": "verify"}], [STEPS[0]] * 2]
)
async def test_ready_rejects_invalid_steps(board: tuple[Ledger, SqliteStore], invalid: Any) -> None:
    ledger, _ = board
    await _poll(ledger, _pull())
    with pytest.raises(LedgerError) as exc:
        await ledger.execute("worker:fixture", "lane_ready", "invalid", _ready(invalid))
    assert exc.value.code == "invalid"
    assert "checklist" not in await _lane(ledger)


@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_typed_ready_and_merge_over_mcp(
    transport: str, tmp_path: Path, token_home: Path, github_fixture: FixtureGitHub
) -> None:
    config = write_config(tmp_path)
    (tmp_path / "provider-token").write_text(TOKEN, encoding="utf-8")
    config.write_text(
        config.read_text()
        + "\n[projects.demo.workers.fixture]\n"
        + f'\n[projects.demo.github]\nrepository = "{REPO}"\n'
        + f'api_url = "{github_fixture.url}"\npoll_seconds = 1\ntoken_file = "provider-token"\n',
        encoding="utf-8",
    )
    local = TokenRegistry(tmp_path / "data").issue("demo", "orchestrator", local=True)
    daemon = start_plotroom("serve", "--config", str(config))
    try:
        port = (await asyncio.to_thread(daemon.wait_for_log, "serving"))["port"]
        url = f"http://127.0.0.1:{port}/mcp"
        async with lane_client(transport, config, url, local.bearer, local.token_id) as client:
            started = await client.call_tool(
                "lane_start",
                {
                    "lane": "lane",
                    "worker": "fixture",
                    "branch": BRANCH,
                    "work_items": [],
                    "request_id": "start",
                },
            )
            assert tool_json(started)["ok"]
            async with asyncio.timeout(10):
                while not tool_json(await client.call_tool("snapshot"))["data"].get("open_prs"):  # noqa: ASYNC110 - daemon subprocess
                    await asyncio.sleep(0.05)
            invalid = await client.call_tool(
                "lane_ready", {**_ready("free text"), "request_id": "bad"}
            )
            assert invalid.is_error
            ready = await client.call_tool("lane_ready", {**_ready(), "request_id": "ready"})
            assert tool_json(ready)["ok"]
            pending = tool_json(await client.call_tool("snapshot", {"lane": "lane"}))["data"][
                "checklist"
            ]
            assert pending["state"] == "pending"
            github_fixture.rows = [pull(state="closed", merged=True, seconds=2)]
            async with asyncio.timeout(10):
                while True:
                    result = tool_json(await client.call_tool("snapshot", {"lane": "lane"}))["data"]
                    if result["checklist"]["state"] == "active":
                        break
                    await asyncio.sleep(0.05)
            assert result["checklist"] == {**pending, "state": "active"}
    finally:
        await asyncio.to_thread(daemon.stop)
