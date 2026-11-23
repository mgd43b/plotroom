"""Once triggers consume committed projection facts through the project writer."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import pytest

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.store_sqlite.ledger_sql import SqliteTransaction
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.actions import Action
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError, NewEvent
from plotroom.core.github import PR_MERGED, PULL_REQUESTS, ObservedPull, apply_pull_poll
from plotroom.core.ledger import Ledger
from plotroom.core.snapshot import render_snapshot
from plotroom.core.trigger_actions import NOTIFICATION_ITEM
from plotroom.core.triggers import TRIGGER_FIRED

REPO = "fixture/trigger-projection"
SHA = "a" * 40
ORCHESTRATOR = "orchestrator"
WORKER = "worker:fixture"


def observed(
    number: int, state: Literal["open", "closed", "merged"], at: int, branch: str | None = None
) -> ObservedPull:
    return ObservedPull(
        number,
        branch or f"topic/{number}",
        SHA,
        REPO,
        state,
        f"2026-11-23T12:00:{at:02d}.000000Z",
        "Synthetic pull request",
    )


def merged(number: int) -> dict[str, Any]:
    return {"pr_merged": {"number": number}}


def notification(predicate: Any, text: str = "Ready", **kwargs: Any) -> dict[str, Any]:
    return {"predicate": predicate, "action": {"notify_orchestrator": {"text": text}}, **kwargs}


@asynccontextmanager
async def open_ledger(
    directory: Path, repository: str = REPO
) -> AsyncGenerator[tuple[Ledger, ProjectRuntime]]:
    clock = FixedClock()
    store = SqliteStore("demo", directory)
    await store.migrate()
    ledger = Ledger(
        store, build_catalog(github_repository=repository), clock, github_repository=repository
    )
    ledger.start()
    try:
        yield ledger, ProjectRuntime(ledger, clock, tick_seconds=60, action_attempts=2)
    finally:
        await ledger.stop()
        await store.close()


async def project(ledger: Ledger, *pulls: ObservedPull) -> None:
    repository = ledger.github_repository
    assert repository is not None
    cursor = await ledger.fetch_cursor(repository, PULL_REQUESTS)
    as_of = max(pull.updated_at for pull in pulls)
    assert await ledger.run(
        "system", lambda uow: apply_pull_poll(uow, repository, cursor, pulls, as_of)
    )


async def test_pr_merge_notifies_once_and_a_late_trigger_fires_at_creation(tmp_path: Path) -> None:
    async with open_ledger(tmp_path) as (ledger, runtime):
        spec = notification(merged(7), "Existing trigger")
        with pytest.raises(LedgerError) as denied:
            await ledger.execute(WORKER, "trigger", "worker-create", spec)
        assert denied.value.code == "forbidden"

        early = await ledger.execute(ORCHESTRATOR, "trigger", "early", spec)
        await project(ledger, observed(7, "open", 0))
        await ledger.append(
            "system",
            NewEvent(
                "github",
                PR_MERGED,
                "unprojected",
                {
                    "repository": REPO,
                    "number": 7,
                    "branch": "topic/7",
                    "head_sha": SHA,
                    "state": "merged",
                    "updated_at": "2026-11-23T12:00:01.000000Z",
                },
            ),
        )
        assert not await runtime.run_one_action()

        await project(ledger, observed(7, "merged", 2))
        pending = await ledger.pending_action()
        assert pending is not None
        assert not (await ledger.read_board(ORCHESTRATOR))[1]
        assert await runtime.run_one_action()
        for sequence in range(3):
            await ledger.append("system", NewEvent("core", "core.tick", f"burst-{sequence}"))
        await project(ledger, observed(7, "merged", 2))
        assert (await ledger.execute(ORCHESTRATOR, "trigger", "early", spec)).replayed
        assert not await runtime.run_one_action()
        [old_item] = (await ledger.read_board(ORCHESTRATOR))[1]
        assert old_item.kind == NOTIFICATION_ITEM.name
        assert old_item.context == {"text": "Existing trigger", "action_key": pending.key}

        late = await ledger.execute(
            ORCHESTRATOR, "trigger", "late", notification(merged(7), "Late trigger")
        )
        assert await runtime.run_one_action()
        assert not await runtime.run_one_action()
        assert [item.context["text"] for item in (await ledger.read_board(ORCHESTRATOR))[1]] == [
            "Existing trigger",
            "Late trigger",
        ]
        fired = [
            event.upstream_id
            for event in await ledger.events_after(0)
            if event.kind == TRIGGER_FIRED
        ]
        assert fired == [early.result["id"], late.result["id"]]


async def test_failed_action_enqueue_rolls_back_projection_and_firing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_ledger(tmp_path) as (ledger, _):
        await ledger.execute(ORCHESTRATOR, "trigger", "create", notification(merged(11)))
        seq = await ledger.head_seq()

        async def fail_enqueue(_transaction: SqliteTransaction, _action: Action) -> None:
            raise RuntimeError("Synthetic action write failure")

        with monkeypatch.context() as patch:
            patch.setattr(SqliteTransaction, "insert_action", fail_enqueue)
            with pytest.raises(RuntimeError, match="Synthetic action write failure"):
                await project(ledger, observed(11, "merged", 1))

        assert await ledger.head_seq() == seq
        assert await ledger.pull_request(REPO, 11) is None
        assert await ledger.pending_action() is None
        assert (await ledger.list_triggers(ORCHESTRATOR, None))[0]["state"] == "open"
        await project(ledger, observed(11, "merged", 1))
        assert await ledger.pending_action() is not None


async def test_branch_glob_binds_first_projection_row_across_restart(tmp_path: Path) -> None:
    async with open_ledger(tmp_path) as (ledger, _):
        await ledger.execute(
            ORCHESTRATOR, "trigger", "glob", notification({"pr_merged": {"branch_glob": "topic/*"}})
        )
        await project(ledger, observed(20, "open", 1), observed(1, "merged", 2))
        [trigger] = await ledger.list_triggers(ORCHESTRATOR, None)
        assert trigger["bindings"] == {"$": 20}
        assert trigger["state"] == "open"
        assert await ledger.pending_action() is None

    async with open_ledger(tmp_path) as (ledger, runtime):
        await project(ledger, observed(2, "merged", 3))
        assert not await runtime.run_one_action()
        await project(ledger, observed(20, "merged", 4, "renamed/20"))
        assert await runtime.run_one_action()
        assert not await runtime.run_one_action()
        [trigger] = await ledger.list_triggers(ORCHESTRATOR, None)
        assert trigger["bindings"] == {"$": 20}
        assert trigger["state"] == "fired"


async def test_exact_pr_identity_keeps_the_registration_repository(tmp_path: Path) -> None:
    async with open_ledger(tmp_path) as (ledger, _):
        await ledger.execute(ORCHESTRATOR, "trigger", "create", notification(merged(7)))

    async with open_ledger(tmp_path, "fixture/other-repository") as (ledger, runtime):
        await project(ledger, observed(7, "merged", 1))
        assert not await runtime.run_one_action()
        [trigger] = await ledger.list_triggers(ORCHESTRATOR, None)
        assert trigger["state"] == "open"
        assert trigger["repository"] == REPO


async def test_glob_uses_first_matching_branch_observation(tmp_path: Path) -> None:
    async with open_ledger(tmp_path) as (ledger, _):
        await project(ledger, observed(20, "open", 1, "other/20"))
        await project(ledger, observed(1, "open", 2))
        await project(ledger, observed(20, "open", 3))
        await ledger.execute(
            ORCHESTRATOR, "trigger", "glob", notification({"pr_merged": {"branch_glob": "topic/*"}})
        )
        [trigger] = await ledger.list_triggers(ORCHESTRATOR, None)
        assert trigger["bindings"] == {"$": 1}


async def test_excessive_predicate_nesting_returns_invalid(tmp_path: Path) -> None:
    async with open_ledger(tmp_path) as (ledger, _):
        predicate = merged(1)
        for _ in range(2000):
            predicate = {"all_of": [predicate]}
        with pytest.raises(LedgerError, match="nested too deeply") as exc:
            await ledger.execute(ORCHESTRATOR, "trigger", "depth", notification(predicate))
        assert exc.value.code == "invalid"
        assert not await ledger.list_triggers(ORCHESTRATOR, None)


async def test_pr_trigger_without_a_configured_repository_is_refused(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    ledger = Ledger(store, build_catalog(), FixedClock())
    ledger.start()
    try:
        with pytest.raises(LedgerError, match="configured GitHub repository") as exc:
            await ledger.execute(ORCHESTRATOR, "trigger", "missing-repo", notification(merged(1)))
        assert exc.value.code == "invalid"
        assert not await ledger.list_triggers(ORCHESTRATOR, None)
    finally:
        await ledger.stop()
        await store.close()


async def test_list_cancel_and_consistent_snapshot_counts(tmp_path: Path) -> None:
    async with open_ledger(tmp_path) as (ledger, runtime):
        await ledger.execute(ORCHESTRATOR, "trigger", "open", notification(merged(8)))
        await ledger.execute(
            ORCHESTRATOR, "trigger", "fire", notification(merged(9), lane="lane-a")
        )
        cancellation = await ledger.execute(
            ORCHESTRATOR,
            "trigger",
            "cancel-me",
            notification({"pr_merged": {"branch_glob": "topic/10"}}, lane="lane-b"),
        )
        await project(ledger, observed(9, "merged", 1), observed(10, "open", 1))
        args = {"trigger_id": cancellation.result["id"]}
        for role in (WORKER, "clerk", "drain"):
            with pytest.raises(LedgerError) as denied:
                await ledger.execute(role, "cancel", "cancel", args)
            assert denied.value.code == "forbidden"
            with pytest.raises(LedgerError) as denied:
                await ledger.list_triggers(role, None)
            assert denied.value.code == "forbidden"
        assert (await ledger.execute(ORCHESTRATOR, "cancel", "cancel", args)).result[
            "state"
        ] == "canceled"
        assert (await ledger.execute(ORCHESTRATOR, "cancel", "cancel", args)).replayed
        await project(ledger, observed(10, "merged", 2))
        assert await runtime.run_one_action()
        assert not await runtime.run_one_action()
        assert [item["state"] for item in await ledger.list_triggers(ORCHESTRATOR, None)] == [
            "open",
            "fired",
            "canceled",
        ]
        [only_lane] = await ledger.list_triggers(ORCHESTRATOR, "lane-b")
        assert only_lane["bindings"] == {"$": 10}
        assert only_lane["state"] == "canceled"
        assert only_lane["action_key"] is None
        (cursor, items, lanes, _), github, counts = await ledger.snapshot_with_triggers(
            ORCHESTRATOR
        )
        board = render_snapshot(
            "demo", cursor, items, lanes, 2000, github=github, trigger_counts=counts
        )
        assert board["triggers"] == {"open": 1, "fired": 1, "canceled": 1}


@pytest.mark.parametrize(("first", "second"), [(1, 2), (2, 1)])
async def test_all_of_waits_for_both_merges_across_restart(
    tmp_path: Path, first: int, second: int
) -> None:
    async with open_ledger(tmp_path) as (ledger, runtime):
        await ledger.execute(
            ORCHESTRATOR, "trigger", "all", notification({"all_of": [merged(1), merged(2)]})
        )
        await project(ledger, observed(first, "merged", 1))
        assert not await runtime.run_one_action()
    async with open_ledger(tmp_path) as (ledger, runtime):
        assert not await runtime.run_one_action()
        await project(ledger, observed(second, "merged", 2))
        assert await runtime.run_one_action()
        assert not await runtime.run_one_action()


async def test_any_of_fires_on_first_merge_and_records_other_branch_bindings(
    tmp_path: Path,
) -> None:
    async with open_ledger(tmp_path) as (ledger, runtime):
        await ledger.execute(
            ORCHESTRATOR,
            "trigger",
            "any",
            notification(
                {
                    "any_of": [
                        merged(1),
                        {"all_of": [{"pr_merged": {"branch_glob": "topic/2"}}, merged(3)]},
                    ]
                }
            ),
        )
        await project(ledger, observed(1, "merged", 1), observed(2, "open", 1))
        assert await runtime.run_one_action()
        assert (await ledger.list_triggers(ORCHESTRATOR, None))[0]["bindings"] == {"$/1/0": 2}
        await project(ledger, observed(2, "merged", 2), observed(3, "merged", 2))
        assert not await runtime.run_one_action()


@pytest.mark.parametrize(
    ("predicate", "reason"),
    [
        ({"all_of": ["when it looks merged"]}, "one leaf or group"),
        ({"not_registered": {}}, "unknown predicate leaf"),
        ({"all_of": []}, "nonempty array"),
        ({"pr_merged": 12}, "arguments must be an object"),
        ({"pr_merged": {"number": True}}, "must be of type int"),
        ({"pr_merged": {"number": 2**63}}, "positive SQLite integer"),
        ({"pr_merged": {"number": 1, "branch_glob": "topic/*"}}, "exactly one"),
        ({"pr_merged": {"branch_glob": " "}}, "nonempty branch pattern"),
        ({"pr_merged": {"number": 1, "text": "ignored?"}}, "unknown field"),
    ],
)
async def test_invalid_predicate_is_refused_before_registration(
    tmp_path: Path, predicate: dict[str, Any], reason: str
) -> None:
    async with open_ledger(tmp_path) as (ledger, _):
        with pytest.raises(LedgerError, match=reason) as rejected:
            await ledger.execute(ORCHESTRATOR, "trigger", "invalid", notification(predicate))
        assert rejected.value.code == "invalid"
        assert await ledger.head_seq() == 0
        assert not await ledger.list_triggers(ORCHESTRATOR, None)
