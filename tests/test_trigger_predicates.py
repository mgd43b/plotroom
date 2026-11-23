"""Typed trigger leaves over committed projections and the durable event log."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError, NewEvent
from plotroom.core.github_projection import (
    CHECKS,
    ISSUES,
    MAIN,
    THREADS,
    GitHubRecord,
    GitHubResource,
)
from plotroom.core.ledger import Ledger
from plotroom.core.trigger_actions import DeliveryRoute
from test_triggers import ORCHESTRATOR, REPO, SHA, WORKER, notification, observed, project

LANE: dict[str, Any] = {"lane": "lane", "worker": "fixture", "branch": "topic", "work_items": []}
OTHER_SHA = "b" * 40
TIME = "2026-11-23T12:00:00.000000Z"


@asynccontextmanager
async def opened(path: Path, clock: FixedClock) -> AsyncGenerator[Ledger]:
    store = SqliteStore("demo", path)
    await store.migrate()
    catalog = build_catalog(
        frozenset({"fixture", "other"}),
        github_repository=REPO,
        image_jobs=("amd64", "arm64"),
        lane_idle_minutes=2,
        delivery_routes={"other": DeliveryRoute("switchboard", "fixture-peer")},
    )
    ledger = Ledger(store, catalog, clock, github_repository=REPO)
    ledger.start()
    try:
        yield ledger
    finally:
        await ledger.stop()
        await store.close()


async def trigger(ledger: Ledger, name: str, args: Any, **options: Any) -> str:
    created = await ledger.execute(
        ORCHESTRATOR, "trigger", name, notification({name: args}, **options)
    )
    return created.result["id"]


async def occurrences(ledger: Ledger, trigger_id: str) -> list[str]:
    return [
        event.id
        for event in await ledger.events_after(0, 1000)
        if event.kind == "core.trigger_fired" and event.fields["trigger_id"] == trigger_id
    ]


async def resource(ledger: Ledger, kind: str, snapshot: GitHubResource, scope: str = "") -> None:
    await ledger.run("system", lambda uow: uow.save_github_resource(REPO, kind, scope, snapshot))


def check(identifier: int, name: str, state: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "name": name,
        "status": "in_progress" if state == "running" else "completed",
        "conclusion": state,
    }


def check_runs(
    *checks: dict[str, Any], complete: bool = True, sha: str = OTHER_SHA
) -> GitHubResource:
    return GitHubResource(
        tuple(
            GitHubRecord(
                str(row["id"]),
                "check_run",
                {**row, "head_sha": sha},
                details={"app_id": row.get("app_id", 1)},
            )
            for row in checks
        ),
        complete=complete,
    )


async def test_pr_green_reads_latest_checks_on_bound_current_head(tmp_path: Path) -> None:
    async with opened(tmp_path, FixedClock()) as ledger:
        fired = await trigger(ledger, "pr_green", {"branch_glob": "topic/*"})
        await project(ledger, observed(7, "open", 0))
        await project(ledger, replace(observed(7, "open", 1), head_sha=OTHER_SHA))
        old = check(1, "ci", "success")
        await resource(ledger, CHECKS, check_runs(old, sha=SHA), SHA)
        await resource(ledger, CHECKS, check_runs(), OTHER_SHA)
        await resource(ledger, CHECKS, check_runs(old, complete=False), OTHER_SHA)
        await resource(ledger, CHECKS, check_runs(old, check(2, "ci", "running")), OTHER_SHA)
        await resource(
            ledger,
            CHECKS,
            check_runs(
                {**check(2, "ci", "failure"), "app_id": None},
                {**check(3, "ci", "success"), "app_id": 2},
            ),
            OTHER_SHA,
        )
        assert await occurrences(ledger, fired) == []
        await resource(ledger, CHECKS, check_runs(old, check(2, "ci", "success")), OTHER_SHA)
        assert len(await occurrences(ledger, fired)) == 1
        [entry] = await ledger.list_triggers(ORCHESTRATOR, None)
        assert entry["bindings"] == {"$": 7}


async def test_threads_and_issue_use_complete_current_resource(tmp_path: Path) -> None:
    async with opened(tmp_path, FixedClock()) as ledger:
        threads = await trigger(ledger, "pr_threads_resolved", {"number": 7})
        closed = await trigger(ledger, "issue_closed", {"number": 17}, mode="every")
        await project(ledger, observed(7, "open", 0))
        prior = await ledger.pull_request(REPO, 7)
        assert prior is not None
        await project(ledger, replace(observed(7, "open", 1), head_sha=OTHER_SHA))
        current = await ledger.pull_request(REPO, 7)
        assert current is not None
        old = {"head_sha": prior.head_sha, "pull_version": prior.version}
        attrs = {"head_sha": current.head_sha, "pull_version": current.version}
        unresolved = GitHubRecord("thread", "review_thread", {"resolved": False})
        resolved = replace(unresolved, fields={"resolved": True})
        await resource(ledger, THREADS, GitHubResource((resolved,), attributes=old), "7")
        await resource(
            ledger,
            THREADS,
            GitHubResource((resolved,), attributes={**attrs, "pull_version": prior.version}),
            "7",
        )
        await resource(ledger, THREADS, GitHubResource((unresolved,), attributes=attrs), "7")
        await resource(
            ledger, THREADS, GitHubResource((resolved,), complete=False, attributes=attrs), "7"
        )
        assert await occurrences(ledger, threads) == []
        await resource(ledger, THREADS, GitHubResource((resolved,), attributes=attrs), "7")
        assert len(await occurrences(ledger, threads)) == 1

        issue = GitHubRecord("issue/17", "issue_closed", {"state": "closed"})
        await resource(ledger, ISSUES, GitHubResource((issue,), complete=False))
        assert await occurrences(ledger, closed) == []
        await resource(ledger, ISSUES, GitHubResource((issue,)))
        await resource(ledger, ISSUES, GitHubResource((issue,)))
        assert len(await occurrences(ledger, closed)) == 1
        reopened = replace(issue, kind="issue_opened", fields={"state": "open"})
        await resource(ledger, ISSUES, GitHubResource((reopened,)))
        await resource(ledger, ISSUES, GitHubResource((issue,)))
        assert len(await occurrences(ledger, closed)) == 2


def main(
    head: str, *jobs: dict[str, Any], verdict_sha: str = SHA, attributed: bool = True
) -> GitHubResource:
    verdict = GitHubRecord(
        "run",
        "workflow_run",
        {**check(1, "ci", "success"), "head_sha": verdict_sha, "jobs": list(jobs)},
        details={"main_branch_run": attributed},
    )
    return GitHubResource((verdict,), attributes={"head_sha": head})


async def test_main_and_run_report_predicates_have_exact_targets(tmp_path: Path) -> None:
    async with opened(tmp_path, FixedClock()) as ledger:
        await ledger.execute(ORCHESTRATOR, "lane_start", "start", LANE)
        run = await trigger(ledger, "run_finished", {"run_id": "123"}, lane="lane")
        images = await trigger(ledger, "main_green_with_images", {"sha": SHA})
        report = {"lane": "lane", "run_id": "123", "state": "running"}
        with pytest.raises(LedgerError, match="does not own"):
            await ledger.execute("worker:other", "run_report", "foreign", report)
        with pytest.raises(LedgerError, match="may not execute"):
            await ledger.execute(ORCHESTRATOR, "run_report", "orchestrator", report)
        await ledger.execute(WORKER, "run_report", "running", report)
        await ledger.execute(
            ORCHESTRATOR,
            "lane_start",
            "start-other",
            {**LANE, "lane": "other-lane", "worker": "other", "branch": "other"},
        )
        with pytest.raises(LedgerError, match="run_id already belongs to another lane"):
            await ledger.execute(
                "worker:other",
                "run_report",
                "foreign-id",
                {"lane": "other-lane", "run_id": "123", "state": "succeeded"},
            )
        assert not await occurrences(ledger, run)
        report["state"] = "succeeded"
        completion = await ledger.execute(
            ORCHESTRATOR,
            "trigger",
            "send-to-another-lane",
            {
                "predicate": {"run_finished": {"run_id": "123"}},
                "action": {"send": {"worker": "other", "text": "Run finished"}},
            },
        )
        done = await ledger.execute(WORKER, "run_report", "finished", report)
        assert len(await occurrences(ledger, completion.result["id"])) == 1
        with pytest.raises(LedgerError, match="finished run cannot return"):
            await ledger.execute(WORKER, "run_report", "late", {**report, "state": "queued"})
        retry = await ledger.execute(WORKER, "run_report", "finished", report)
        assert retry.replayed
        assert done.result == retry.result
        assert (
            await ledger.execute(WORKER, "run_report", "duplicate-fact", report)
        ).result == done.result
        assert len(await occurrences(ledger, run)) == 1
        assert [
            event.fields["state"]
            for event in await ledger.events_after(0)
            if event.kind == "run.status"
        ] == ["running", "succeeded"]

        amd = check(2, "amd64", "success")
        arm = check(3, "arm64", "success")
        await resource(ledger, MAIN, main(OTHER_SHA, amd, arm))
        await resource(ledger, MAIN, main(SHA, amd, arm, verdict_sha=OTHER_SHA))
        await resource(ledger, MAIN, main(SHA, amd, arm, attributed=False))
        await resource(ledger, MAIN, main(SHA, amd))
        await resource(ledger, MAIN, main(SHA, amd, arm, check(4, "arm64", "failure")))
        assert not await occurrences(ledger, images)
        await resource(ledger, MAIN, main(SHA, amd, arm))
        assert len(await occurrences(ledger, images)) == 1


async def test_every_message_and_deploy_use_committed_typed_facts(tmp_path: Path) -> None:
    clock = FixedClock()
    first = NewEvent(
        "switchboard", "switchboard.message", "1", {"sender": "remote", "worker": "fixture"}
    )
    second = replace(first, upstream_id="2")
    async with opened(tmp_path, clock) as ledger:
        await ledger.execute(ORCHESTRATOR, "lane_start", "start", LANE)
        await ledger.append("system", replace(first, upstream_id="before"))
        messages = await trigger(ledger, "message_from", {"worker": "fixture"}, mode="every")
        deploy = await trigger(ledger, "deploy_finished", {"environment": "test", "sha": SHA})
        await ledger.append(
            "system", replace(first, upstream_id="foreign", fields={"sender": "fixture"})
        )
        assert not await occurrences(ledger, messages)
        await ledger.append("system", first)
        await ledger.append("system", first)
        assert len(await occurrences(ledger, messages)) == 1
        report = {"lane": "lane", "environment": "test", "sha": SHA, "state": "queued"}
        await ledger.execute(WORKER, "deploy_report", "queued", report)
        await ledger.execute(
            WORKER, "deploy_report", "wrong", {**report, "sha": OTHER_SHA, "state": "success"}
        )
        assert not await occurrences(ledger, deploy)
        await ledger.execute(WORKER, "deploy_report", "match", {**report, "state": "success"})
        assert len(await occurrences(ledger, deploy)) == 1

    async with opened(tmp_path, clock) as ledger:
        await ledger.append("system", first)
        assert len(await occurrences(ledger, messages)) == 1
        await ledger.append("system", second)
        await ledger.append("system", second)
        assert len(set(await occurrences(ledger, messages))) == 2
        await ledger.execute(ORCHESTRATOR, "cancel", "cancel-every", {"trigger_id": messages})
        await ledger.append("system", replace(first, upstream_id="3"))
        assert len(await occurrences(ledger, messages)) == 2


async def test_idle_rearms_after_activity_and_requires_a_tick(tmp_path: Path) -> None:
    clock = FixedClock()
    async with opened(tmp_path, clock) as ledger:
        await ledger.execute(ORCHESTRATOR, "lane_start", "start", LANE)
        idle = await trigger(ledger, "lane_idle", {"lane": "lane"}, mode="every", lane="lane")
        runtime = ProjectRuntime(ledger, clock, tick_seconds=1, action_attempts=1)
        clock.advance(119)
        assert await runtime.tick_if_due()
        clock.advance(1)
        assert not await occurrences(ledger, idle)
        assert await runtime.tick_if_due()
        assert len(await occurrences(ledger, idle)) == 1

    async with opened(tmp_path, clock) as ledger:
        runtime = ProjectRuntime(ledger, clock, tick_seconds=1, action_attempts=1)
        clock.advance(240)
        assert await runtime.tick_if_due()
        assert len(await occurrences(ledger, idle)) == 1
        await ledger.execute(WORKER, "lane_update", "active", {"lane": "lane", "waiting_on": ""})
        clock.advance(119)
        assert await runtime.tick_if_due()
        assert len(await occurrences(ledger, idle)) == 1
        clock.advance(1)
        assert await runtime.tick_if_due()
        assert len(await occurrences(ledger, idle)) == 2


@pytest.mark.parametrize(
    ("leaf", "args"),
    [
        ("pr_green", {"number": 1, "branch_glob": "*"}),
        ("issue_closed", {"number": 2**65}),
        ("run_finished", {"run_id": ""}),
        ("main_green_with_images", {"sha": "a"}),
        ("deploy_finished", {"environment": " ", "sha": SHA}),
        ("lane_idle", {"lane": "\0"}),
        ("message_from", {"worker": "user supplied prose"}),
    ],
)
async def test_new_leaves_refuse_untyped_or_inexact_selectors(
    tmp_path: Path, leaf: str, args: dict[str, Any]
) -> None:
    async with opened(tmp_path, FixedClock()) as ledger:
        with pytest.raises(LedgerError) as refused:
            await trigger(ledger, leaf, args)
        assert refused.value.code == "invalid"
        assert await ledger.list_triggers(ORCHESTRATOR, None) == []


async def test_composed_every_trigger_consumes_events_only_on_fire(tmp_path: Path) -> None:
    async with opened(tmp_path, FixedClock()) as ledger:
        created = await ledger.execute(
            ORCHESTRATOR,
            "trigger",
            "composed",
            notification(
                {"all_of": [{"pr_merged": {"number": 7}}, {"message_from": {"worker": "fixture"}}]},
                mode="every",
            ),
        )
        identity = created.result["id"]
        message = NewEvent(
            "switchboard", "switchboard.message", "queued", {"sender": "peer", "worker": "fixture"}
        )
        await ledger.append("system", message)
        assert not await occurrences(ledger, identity)
        await project(ledger, observed(7, "merged", 0))
        assert len(await occurrences(ledger, identity)) == 1
        await ledger.append("system", message)
        assert len(await occurrences(ledger, identity)) == 1
        await ledger.append("system", replace(message, upstream_id="fresh"))
        assert len(await occurrences(ledger, identity)) == 2


async def test_overlapping_message_does_not_keep_a_composed_trigger_true(tmp_path: Path) -> None:
    clock = FixedClock()
    async with opened(tmp_path, clock) as ledger:
        created = await ledger.execute(
            ORCHESTRATOR,
            "trigger",
            "either",
            notification(
                {
                    "any_of": [
                        {"issue_closed": {"number": 7}},
                        {"message_from": {"worker": "fixture"}},
                    ]
                },
                mode="every",
            ),
        )
        identity = created.result["id"]
        closed = GitHubRecord("issue/7", "issue_closed", {"state": "closed"})
        await resource(ledger, ISSUES, GitHubResource((closed,)))
        overlap = NewEvent(
            "switchboard", "switchboard.message", "overlap", {"sender": "peer", "worker": "fixture"}
        )
        await ledger.append("system", overlap)
        assert len(await occurrences(ledger, identity)) == 1

    async with opened(tmp_path, clock) as ledger:
        reopened = replace(closed, kind="issue_opened", fields={"state": "open"})
        await resource(ledger, ISSUES, GitHubResource((reopened,)))
        await ledger.append("system", overlap)
        assert len(await occurrences(ledger, identity)) == 1
        await ledger.append("system", replace(overlap, upstream_id="fresh"))
        assert len(await occurrences(ledger, identity)) == 2


async def test_main_images_trigger_requires_configured_image_evidence(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    ledger = Ledger(store, build_catalog(github_repository=REPO), FixedClock())
    ledger.start()
    try:
        images = await trigger(ledger, "main_green_with_images", {})
        await resource(ledger, MAIN, main(SHA))
        assert await occurrences(ledger, images) == []
    finally:
        await ledger.stop()
        await store.close()


@pytest.mark.parametrize("message_before_fire", [True, False])
async def test_nested_events_use_the_shared_last_fire_and_per_leaf_plateau_cursors(
    tmp_path: Path, message_before_fire: bool
) -> None:
    clock = FixedClock()
    async with opened(tmp_path, clock) as ledger:
        created = await ledger.execute(
            ORCHESTRATOR,
            "trigger",
            "nested-branch",
            notification(
                {
                    "any_of": [
                        {"issue_closed": {"number": 7}},
                        {
                            "all_of": [
                                {"issue_closed": {"number": 8}},
                                {"message_from": {"worker": "fixture"}},
                            ]
                        },
                    ],
                },
                mode="every",
            ),
        )
        identity = created.result["id"]
        closed = GitHubRecord("issue/7", "issue_closed", {"state": "closed"})
        message = NewEvent(
            "switchboard", "switchboard.message", "nested", {"sender": "peer", "worker": "fixture"}
        )
        if message_before_fire:
            await ledger.append("system", message)
        await resource(ledger, ISSUES, GitHubResource((closed,)))
        if not message_before_fire:
            await ledger.append("system", message)
        assert len(await occurrences(ledger, identity)) == 1

    async with opened(tmp_path, clock) as ledger:
        reopened = replace(closed, kind="issue_opened", fields={"state": "open"})
        await resource(ledger, ISSUES, GitHubResource((reopened,)))
        assert len(await occurrences(ledger, identity)) == 1
        second = GitHubRecord("issue/8", "issue_closed", {"state": "closed"})
        await resource(ledger, ISSUES, GitHubResource((reopened, second)))
        expected = 1 if message_before_fire else 2
        assert len(await occurrences(ledger, identity)) == expected
        if message_before_fire:
            await ledger.append("system", replace(message, upstream_id="fresh"))
            assert len(await occurrences(ledger, identity)) == 2
