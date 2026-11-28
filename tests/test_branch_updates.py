"""Deterministic Rule 4 observations and fixture-only effects."""

from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from fakes import FixedClock, commit_status_evidence
from plotroom.adapters.github.pull_comparison import fetch_pull_comparison
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.actions import ACTION_PAGE_SIZE, Action
from plotroom.core.branch_updates import (
    BRANCH_UPDATE_CONFLICT,
    BRANCH_UPDATE_STAGED,
    BranchUpdate,
    stage_branch_update,
)
from plotroom.core.catalog import build_catalog
from plotroom.core.events import NewEvent, format_time
from plotroom.core.github import ACTIVE_PULL_DETAIL_BUDGET, PULL_REQUESTS, FetchCursor, PullRequest
from plotroom.core.github_projection import (
    CHECKS,
    COMMIT_STATUSES,
    LABELS,
    MAIN,
    MAIN_CI,
    PULL_TEXT,
    THREADS,
    GitHubRecord,
    GitHubResource,
)
from plotroom.core.lanes import Lane
from plotroom.core.ledger import Ledger
from plotroom.core.merge_gate import GatePolicy
from plotroom.core.merge_overlap import PULL_COMPARISON, bind_overlap_labels
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.github_write import BranchUpdateReceipt, FixtureGitHubWrite

REPO = "example/demo"
API = "https://api.github.test"
HEAD, BASE, TIP, NEXT = (value * 40 for value in "abcd")
WORKFLOW = "ci.yml"
KEY = f"branch_update:7:{HEAD}"
POLICY = GatePolicy(
    1, ci_workflow=WORKFLOW, fix_label="fix-main", authorized_label_logins=("owner",)
)


class FixtureWrite(FixtureGitHubWrite):
    def __init__(self, *, lose_response: bool = False, fail_lookup: bool = False) -> None:
        self.lose_response = lose_response
        self.fail_lookup = fail_lookup
        self.current_head = HEAD
        self.current_main = TIP
        self.calls: list[str] = []
        self.lookups: list[str] = []
        self.effects: dict[str, BranchUpdateReceipt] = {}

    async def update_branch(
        self,
        *,
        repository: str,
        number: int,
        action_key: str,
        expected_head_sha: str,
        expected_main_sha: str | None,
        recovery_only: bool = False,
    ) -> BranchUpdateReceipt | None:
        assert (repository, number) == (REPO, 7)
        if recovery_only:
            assert expected_main_sha is None
            self.lookups.append(action_key)
            if self.fail_lookup:
                self.fail_lookup = False
                raise TimeoutError("temporary receipt lookup failure")
            return self.effects.get(action_key)
        self.calls.append(action_key)
        if action_key in self.effects:
            return self.effects[action_key]
        if (expected_head_sha, expected_main_sha) != (self.current_head, self.current_main):
            return None
        self.effects[action_key] = BranchUpdateReceipt("conflict", self.current_main)
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError("response lost after fixture effect")
        return self.effects[action_key]

    async def merge(
        self,
        *,
        repository: str,
        number: int,
        action_key: str,
        expected_head_sha: str,
        expected_main_sha: str,
        method: str,
        evidence_revision: str,
    ) -> bool:
        raise AssertionError("the branch-update fixture runner must never merge")


async def setup(
    tmp_path: Path,
    clock: FixedClock,
    port: FixtureWrite | None,
    *,
    lane: bool = True,
    attempts: int = 3,
) -> tuple[SqliteStore, Ledger, ProjectRuntime]:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    catalog = build_catalog(frozenset({"worker1"}), github_repository=REPO)
    ledger = Ledger(store, catalog, clock, github_repository=REPO, merge_gate_policy=POLICY)
    ledger.github_error = None
    ledger.start()
    if lane:
        await ledger.run("system", lambda uow: uow.save_lane(Lane("lane1", "worker1", (), "work")))
    runtime = ProjectRuntime(
        ledger, clock, tick_seconds=5, action_attempts=attempts, fixture_github_write=port
    )
    return store, ledger, runtime


async def observe(
    ledger: Ledger,
    *,
    head: str = HEAD,
    branch: str = "work",
    main: str = TIP,
    version: int = 1,
    state: str = "open",
    fork: bool = False,
    complete: bool = True,
    overlap: bool = True,
    main_red: bool = False,
    fix_label: bool = False,
    unresolved: bool = False,
    pending: bool = False,
    label_id: int | None = None,
    actor_type: str = "User",
) -> None:
    async def write(uow: UnitOfWork) -> None:
        now = format_time(uow.now())
        pull = PullRequest(
            REPO, 7, branch, head, "fork/demo" if fork else REPO, state, now, now, True, version
        )
        await uow.save_pull_request(pull)
        await uow.save_fetch_cursor(REPO, PULL_REQUESTS, FetchCursor(now, now, 1))
        pins = {"head_sha": head, "pull_version": version, "pull_updated_at": now}
        comparison = {
            **pins,
            "branch": "main",
            "main_tip": main,
            "merge_base": BASE,
            "pull_paths": ["a.txt", "b.txt"],
            "main_paths": ["b.txt", "a.txt"] if overlap else ["c.txt"],
        }
        refs = {"branch": "main", "head_sha": main}
        run = GitHubRecord(
            "1/1",
            "workflow_run",
            {
                "id": 1,
                "head_sha": main,
                "status": "completed",
                "conclusion": "startup_failure" if main_red else "success",
            },
        )
        check = GitHubRecord(
            "1",
            "check_run",
            {
                "id": 1,
                "name": "tests",
                "head_sha": head,
                "status": "queued" if pending else "completed",
                "conclusion": None if pending else "success",
            },
        )
        thread = GitHubRecord(
            "thread", "review_thread", {"id": "thread", "number": 7, "resolved": False}
        )
        for resource, scope, snapshot in (
            (CHECKS, head, GitHubResource((check,), as_of=now)),
            (COMMIT_STATUSES, head, commit_status_evidence(REPO, head, now)),
            (MAIN, "", GitHubResource((), attributes=refs, as_of=now)),
            (
                MAIN_CI,
                "",
                GitHubResource((run,), attributes={**refs, "workflow": WORKFLOW}, as_of=now),
            ),
            (
                THREADS,
                "7",
                GitHubResource((thread,) if unresolved else (), attributes=pins, as_of=now),
            ),
            (
                PULL_COMPARISON,
                "7",
                GitHubResource((), complete=complete, attributes=comparison, as_of=now),
            ),
            (
                PULL_TEXT,
                "7",
                GitHubResource(
                    (),
                    attributes={**pins, "body": "", "commit_count": 1, "commit_messages": ["Refs"]},
                    as_of=now,
                ),
            ),
        ):
            await uow.save_github_resource(REPO, resource, scope, snapshot)
        labels = {"fix-main": "owner"} if fix_label else {}
        label_details: dict[str, dict[str, str | int]] = (
            {"fix-main": {"login": "owner", "actor_type": "User", "event_id": 101}}
            if fix_label
            else {}
        )
        records: tuple[GitHubRecord, ...] = ()
        if label_id is not None:
            labels["allow-overlap"] = "owner"
            label_details["allow-overlap"] = {
                "login": "owner",
                "actor_type": actor_type,
                "event_id": label_id,
            }
            records = (
                GitHubRecord(
                    str(label_id),
                    "pull_label",
                    {"name": "allow-overlap", "action": "labeled"},
                ),
            )
        evidence = GitHubResource(
            records,
            attributes={**pins, "labels": labels, "label_provenance": label_details},
            as_of=now,
        )
        previous = await uow.github_resource(REPO, LABELS, "7")
        await uow.save_github_resource(
            REPO, LABELS, "7", bind_overlap_labels(evidence, previous, pull)
        )

    await ledger.run("system", write)


async def test_earlier_open_lane_pull_blocks_queued_update_and_gate(tmp_path: Path) -> None:
    port = FixtureWrite()
    store, ledger, runtime = await setup(tmp_path, FixedClock(), port, lane=False)

    async def add_earlier_pulls(uow: UnitOfWork) -> None:
        candidate = await uow.pull_request(REPO, 7)
        assert candidate is not None
        for number, branch, repo in (
            (5, "work/first", REPO),
            (3, "work/oldest", REPO),
            (2, "Work/case-difference", REPO),
            (1, "work/fork", "fork/demo"),
        ):
            await uow.save_pull_request(
                replace(candidate, number=number, branch=branch, head_repository=repo)
            )

    async def close(uow: UnitOfWork, number: int) -> None:
        pull = await uow.pull_request(REPO, number)
        assert pull is not None
        await uow.save_pull_request(replace(pull, state="closed", version=pull.version + 1))

    try:
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "work-lane",
            {
                "lane": "lane1",
                "worker": "worker1",
                "branch": "work",
                "branch_pattern": "work/*",
                "work_items": [],
            },
        )
        await observe(ledger, branch="work/second")
        assert await runtime.stage_branch_update(7)
        await ledger.run("system", add_earlier_pulls)
        assert (await ledger.merge_gate("clerk", 7)).reasons[0].code == "stale_ci"
        assert not await runtime.run_one_action()
        assert port.lookups == [KEY]
        assert not port.calls

        await observe(ledger, branch="work/second", overlap=False)
        snapshot = await store.read_merge_gate(REPO, 7)
        assert snapshot.lane is not None
        assert snapshot.lane.branch_pattern == "work/*"
        assert snapshot.earlier_pull == 3
        reason = (await ledger.merge_gate("clerk", 7)).reasons[0]
        assert reason.code == "duplicate_lane_pr"
        assert "PR #3" in reason.detail
        await ledger.run("system", lambda uow: close(uow, 3))
        assert (await store.read_merge_gate(REPO, 7)).earlier_pull == 5
        await ledger.run("system", lambda uow: close(uow, 5))
        assert (await store.read_merge_gate(REPO, 7)).earlier_pull is None
        assert (await ledger.merge_gate("clerk", 7)).verdict == "MERGE"
    finally:
        await ledger.stop()
        await store.close()


async def test_overloaded_merge_capacity_preserves_eligible_branch_updates(tmp_path: Path) -> None:
    port = FixtureWrite()
    store, ledger, runtime = await setup(tmp_path, FixedClock(), port)

    async def overload(uow: UnitOfWork) -> None:
        candidate = await uow.pull_request(REPO, 7)
        assert candidate is not None
        for number in range(100, 100 + ACTIVE_PULL_DETAIL_BUDGET):
            await uow.save_pull_request(replace(candidate, number=number, branch=f"pr-{number}"))

    try:
        await observe(ledger, pending=True)
        await ledger.run("system", overload)
        verdict = await ledger.merge_gate("clerk", 7)
        assert [reason.code for reason in verdict.reasons] == ["detail_capacity_exceeded"]
        assert not await runtime.stage_branch_update(7)
        await observe(ledger)
        assert await runtime.stage_branch_update(7)
        assert await runtime.run_one_action()
        assert port.calls == [KEY]
    finally:
        await ledger.stop()
        await store.close()


@pytest.mark.parametrize(
    "after", ["unchanged", "moved", "closed", "merged", "main_moved", "main_moved_after_crash"]
)
async def test_conflict_survives_lost_response_and_restart(tmp_path: Path, after: str) -> None:
    clock, port = FixedClock(), FixtureWrite(lose_response=True)
    attempts = 1 if after == "moved" else 3
    store, ledger, runtime = await setup(tmp_path, clock, port, attempts=attempts)
    try:
        await observe(ledger)
        view = await ledger.merge_gate("clerk", 7)
        assert (view.verdict, view.exit_class, view.head_sha) == ("REFUSE", 3, HEAD)
        assert view.reasons[0].detail.endswith("\n  a.txt\n  b.txt")
        assert view.as_of == format_time(clock.now())
        assert await runtime.stage_branch_update(7)
        await observe(ledger)
        assert not await runtime.stage_branch_update(7)
        action = await ledger.pending_action()
        assert action is not None
        assert action.key == KEY
        assert action.attempts == 0
        if after == "main_moved_after_crash":
            # Process death preempted the local receipt after the fixture effect.
            with pytest.raises(TimeoutError, match="response lost"):
                await port.update_branch(
                    repository=REPO,
                    number=7,
                    action_key=KEY,
                    expected_head_sha=HEAD,
                    expected_main_sha=TIP,
                )
            assert (pending := await ledger.pending_action()) is not None
            assert pending.attempts == 0
        else:
            assert await runtime.run_one_action()
        assert port.effects == {KEY: BranchUpdateReceipt("conflict", TIP)}
        assert not (await ledger.read_board("worker:worker1"))[1]
    finally:
        await ledger.stop()
        await store.close()

    store, ledger, runtime = await setup(tmp_path, clock, port, lane=False, attempts=attempts)
    try:
        if after == "moved":
            await observe(ledger, head=NEXT, branch="other", main=NEXT, version=2)
        elif after in {"closed", "merged"}:
            await observe(ledger, state=after, main=NEXT, version=2)
        elif after in {"main_moved", "main_moved_after_crash"}:
            port.current_main = NEXT
            await observe(ledger, main=NEXT, overlap=False)
            verdict = await ledger.merge_gate("clerk", 7)
            assert (verdict.verdict, verdict.head_sha) == ("MERGE", HEAD)
        else:
            await observe(ledger)
            assert not await runtime.stage_branch_update(7)
        assert await runtime.run_one_action()
        assert not await runtime.run_one_action()
        assert port.calls == ([KEY, KEY] if after == "unchanged" else [KEY])
        assert port.lookups == ([] if after == "unchanged" else [KEY])
        assert port.effects == {KEY: BranchUpdateReceipt("conflict", TIP)}
        items = (await ledger.read_board("worker:worker1"))[1]
        assert [(item.kind, item.subject_ref) for item in items] == [(BRANCH_UPDATE_CONFLICT, KEY)]
        assert items[0].context["lane"] == "lane1"
        assert items[0].context["main_sha"] == TIP
        events = await ledger.events_after(0)
        [completed] = [event for event in events if event.kind == "core.action_done"]
        assert completed.fields["attempt"] == (2 if after == "unchanged" else 1)
        assert [event.upstream_id for event in events if event.kind == BRANCH_UPDATE_STAGED] == [
            KEY
        ]
    finally:
        await ledger.stop()
        await store.close()


async def test_pattern_owner_receives_one_conflict_item(tmp_path: Path) -> None:
    port = FixtureWrite()
    store, ledger, runtime = await setup(tmp_path, FixedClock(), port, lane=False)
    lane = Lane("feature", "worker1", (), "delivery", branch_pattern="work/*")
    try:
        await ledger.run("system", lambda uow: uow.save_lane(lane))
        await observe(ledger, branch="work/83")
        assert (await store.read_merge_gate(REPO, 7)).lane == lane
        assert await runtime.stage_branch_update(7)
        assert await runtime.run_one_action()
        assert not await runtime.run_one_action()
        assert port.calls == [KEY]

        [item] = (await ledger.read_board("worker:worker1"))[1]
        assert item.kind == BRANCH_UPDATE_CONFLICT
        assert (item.role, item.subject_ref, item.context["lane"]) == (
            "worker:worker1",
            KEY,
            lane.id,
        )
        assert item.suggested_actions
    finally:
        await ledger.stop()
        await store.close()


@pytest.mark.parametrize(
    ("change", "owner"),
    [
        ("unchanged", "pattern"),
        ("exact", "exact"),
        ("ambiguous_pattern", None),
        ("ambiguous_exact", None),
        ("archived", None),
        ("moved", None),
    ],
)
async def test_recovered_conflict_routes_to_an_unambiguous_owner(
    tmp_path: Path, change: str, owner: str | None
) -> None:
    clock, port = FixedClock(), FixtureWrite(lose_response=True)
    branch = "topic/17"
    pattern = Lane("pattern", "worker1", (), "topic/primary", branch_pattern="topic/*")
    archived = Lane("old", "worker3", (), branch, state="archived", branch_pattern="topic/*")
    exact = Lane("exact", "worker2", (), branch)
    changes = {
        "exact": (exact,),
        "ambiguous_pattern": (
            Lane("other", "worker2", (), "topic/secondary", branch_pattern="topic/*"),
        ),
        "ambiguous_exact": (exact, Lane("other", "worker3", (), branch)),
        "archived": (replace(pattern, state="archived"),),
    }

    store, ledger, runtime = await setup(tmp_path, clock, port, lane=False)
    try:
        await ledger.run("system", lambda uow: uow.save_lane(pattern))
        await ledger.run("system", lambda uow: uow.save_lane(archived))
        await observe(ledger, branch=branch)
        assert (await store.read_merge_gate(REPO, 7)).lane == pattern
        assert await runtime.stage_branch_update(7)
        assert await runtime.run_one_action()
        assert (await ledger.read_board("orchestrator"))[1] == []
    finally:
        await ledger.stop()
        await store.close()

    store, ledger, runtime = await setup(tmp_path, clock, port, lane=False)
    try:
        for lane in changes.get(change, ()):
            await ledger.run("system", lambda uow, lane=lane: uow.save_lane(lane))
        if change == "moved":
            await observe(ledger, branch=branch, head=NEXT, version=2)
        else:
            await observe(ledger, branch=branch, main=NEXT, overlap=False)
        assert await runtime.run_one_action()
        assert not await runtime.run_one_action()
        assert (port.calls, port.lookups) == ([KEY], [KEY])
        [item] = (await ledger.read_board("orchestrator"))[1]
        assert (item.kind, item.subject_ref, item.context["lane"]) == (
            BRANCH_UPDATE_CONFLICT,
            KEY,
            owner,
        )
        role = {"pattern": "worker:worker1", "exact": "worker:worker2"}.get(owner or "")
        assert item.role == (role or "orchestrator")
        for worker_role in ("worker:worker1", "worker:worker2", "worker:worker3"):
            assert (await ledger.read_board(worker_role))[1] == (
                [item] if role == worker_role else []
            )
    finally:
        await ledger.stop()
        await store.close()


async def test_live_runtime_skips_fixture_queue_and_frozen_action_resumes(tmp_path: Path) -> None:
    clock, port = FixedClock(), FixtureWrite()
    store, ledger, fixture = await setup(tmp_path, clock, port, lane=False)
    ordinary_calls: list[str] = []

    async def ordinary(action: Action) -> None:
        ordinary_calls.append(action.kind)

    async def enqueue(uow: UnitOfWork) -> None:
        cause = (await uow.append(NewEvent("core", "core.tick", "fixture-tick"))).event
        await uow.enqueue_action(cause, "ordinary", {})

    try:
        await observe(ledger)
        normal = ProjectRuntime(
            ledger, clock, tick_seconds=5, action_attempts=3, handlers={"ordinary": ordinary}
        )
        assert not await normal.stage_branch_update(7)
        assert await fixture.stage_branch_update(7)
        assert not await normal.run_one_action()
        await ledger.run("system", enqueue)
        assert await normal.run_one_action()
        assert ordinary_calls == ["ordinary"]
        await ledger.execute("orchestrator", "freeze", "freeze", {"reason": "fixture deploy"})
        assert not await fixture.run_one_action()
        assert not port.calls
        assert not port.lookups

        async def enqueue_later(uow: UnitOfWork) -> None:
            cause = (await uow.append(NewEvent("core", "core.tick", "later-tick"))).event
            await uow.enqueue_action(cause, "ordinary", {})

        fixture.handlers = {"ordinary": ordinary}
        await ledger.run("system", enqueue_later)
        assert await fixture.run_one_action()
        assert ordinary_calls == ["ordinary", "ordinary"]
        assert not port.calls

        await ledger.execute("orchestrator", "unfreeze", "unfreeze", {})
        assert await fixture.run_one_action()
        assert not await fixture.run_one_action()
        assert port.calls == [KEY]
        items = (await ledger.read_board("orchestrator"))[1]
        assert [(item.kind, item.context["lane"]) for item in items] == [
            (BRANCH_UPDATE_CONFLICT, None)
        ]
    finally:
        await ledger.stop()
        await store.close()


async def test_lookup_failure_preserves_the_write_attempt(tmp_path: Path) -> None:
    port = FixtureWrite(fail_lookup=True)
    store, ledger, runtime = await setup(tmp_path, FixedClock(), port, attempts=1)
    try:
        await observe(ledger)
        assert await runtime.stage_branch_update(7)
        await observe(ledger, overlap=False)
        assert not await runtime.run_one_action()
        assert (pending := await ledger.pending_action()) is not None
        assert pending.attempts == 0
        assert not port.calls
        assert port.lookups == [KEY]

        await observe(ledger)
        assert await runtime.run_one_action()
        assert await ledger.pending_action() is None
        assert port.calls == [KEY]
    finally:
        await ledger.stop()
        await store.close()


async def test_deferred_queue_pages_allow_unrelated_work(tmp_path: Path) -> None:
    port = FixtureWrite()
    store, ledger, runtime = await setup(tmp_path, FixedClock(), port, lane=False)
    updates = [
        BranchUpdate(REPO, 7, "work", f"{index:040x}", TIP)
        for index in range(ACTION_PAGE_SIZE * 2 + 1)
    ]
    ordinary_calls: list[str] = []

    async def ordinary(action: Action) -> None:
        ordinary_calls.append(action.kind)

    async def enqueue(uow: UnitOfWork) -> None:
        for update in updates:
            await stage_branch_update(uow, update)
        cause = (await uow.append(NewEvent("core", "core.tick", "after-deferred-pages"))).event
        await uow.enqueue_action(cause, "ordinary", {})

    try:
        runtime.handlers = {"ordinary": ordinary}
        await ledger.run("system", enqueue)
        assert await runtime.run_one_action()
        assert ordinary_calls == ["ordinary"]
        assert port.lookups == [update.key for update in updates]
        assert not port.calls
        assert (first := await ledger.pending_action()) is not None
        assert first.key == updates[0].key
        assert first == await store.pending_action()
    finally:
        await ledger.stop()
        await store.close()


async def test_merged_pull_retires_only_after_receipt_is_confirmed_absent(tmp_path: Path) -> None:
    port = FixtureWrite(fail_lookup=True)
    store, ledger, runtime = await setup(tmp_path, FixedClock(), port, lane=False)
    try:
        await observe(ledger)
        assert await runtime.stage_branch_update(7)
        await observe(ledger, state="merged", version=2)
        assert not await runtime.run_one_action()
        assert await ledger.pending_action() is not None
        assert await runtime.run_one_action()
        assert await ledger.pending_action() is None
        assert port.lookups == [KEY, KEY]
        assert not port.calls
        assert not (await ledger.read_board("orchestrator"))[1]
    finally:
        await ledger.stop()
        await store.close()


@pytest.mark.parametrize("movement", ["base", "head", "closed"])
async def test_defer_until_refs_return_to_eligibility(tmp_path: Path, movement: str) -> None:
    clock, port = FixedClock(), FixtureWrite()
    store, ledger, runtime = await setup(tmp_path, clock, port, lane=False)
    try:
        await observe(ledger)
        assert await runtime.stage_branch_update(7)
        await observe(ledger, main_red=True, fix_label=True)
        assert (await ledger.merge_gate("clerk", 7)).exit_class == 3
        assert not await runtime.run_one_action()
        await observe(ledger, complete=False)
        assert (await ledger.merge_gate("clerk", 7)).reasons[0].code == "overlap_unreadable"
        assert not await runtime.run_one_action()
        await observe(ledger)
        clock.advance(121)
        assert not await runtime.run_one_action()
        assert not port.calls
        assert port.lookups == [KEY] * 3
        clock.advance(1)
        await observe(ledger)
        port.current_main = NEXT
        assert not await runtime.run_one_action()
        assert port.calls == [KEY]
        assert not port.effects
        if movement == "base":
            await observe(ledger, main=NEXT)
        elif movement == "head":
            await observe(ledger, head=NEXT, main=NEXT, version=2)
        else:
            await observe(ledger, state="closed", version=2)
        if movement != "base":
            assert not await runtime.run_one_action()
            assert (pending := await ledger.pending_action()) is not None
            assert pending.key == KEY
            assert port.calls == [KEY]
            assert port.lookups == [KEY] * 4
            assert not port.effects
            await observe(ledger, main=NEXT, version=3)
            assert not await runtime.stage_branch_update(7)
        assert await runtime.run_one_action()
        assert await ledger.pending_action() is None
        assert port.calls == [KEY, KEY]
        assert port.effects == {KEY: BranchUpdateReceipt("conflict", NEXT)}
    finally:
        await ledger.stop()
        await store.close()


async def test_only_fresh_human_waiver_for_observed_head_and_earlier_guards(tmp_path: Path) -> None:
    clock, port = FixedClock(), FixtureWrite()
    store, ledger, runtime = await setup(tmp_path, clock, port)
    try:
        await observe(ledger, pending=True)
        assert (await ledger.merge_gate("clerk", 7)).reasons[0].code == "checks_pending"
        assert not await runtime.stage_branch_update(7)
        await observe(ledger, unresolved=True)
        assert (await ledger.merge_gate("clerk", 7)).reasons[0].code == "review_threads_unresolved"
        assert not await runtime.stage_branch_update(7)
        await observe(ledger, main_red=True)
        assert (await ledger.merge_gate("clerk", 7)).reasons[0].code == "main_red"
        assert not await runtime.stage_branch_update(7)
        await observe(ledger, fork=True)
        assert (await ledger.merge_gate("clerk", 7)).exit_class == 3
        assert not await runtime.stage_branch_update(7)
        await observe(ledger, overlap=False)
        assert (await ledger.merge_gate("clerk", 7)).verdict == "MERGE"
        assert not await runtime.stage_branch_update(7)
        await observe(ledger, head=NEXT, version=2, label_id=1)
        assert (await ledger.merge_gate("clerk", 7)).exit_class == 3
        await observe(ledger, head=NEXT, version=2, label_id=2, actor_type="Bot")
        assert (await ledger.merge_gate("clerk", 7)).exit_class == 3
        await observe(ledger, head=NEXT, version=2, label_id=3)
        assert (await ledger.merge_gate("clerk", 7)).verdict == "MERGE"
        assert not await runtime.stage_branch_update(7)
        await observe(ledger, version=3, label_id=3)
        assert (await ledger.merge_gate("clerk", 7)).exit_class == 3
        await observe(ledger, head=NEXT, version=4, label_id=3)
        assert (await ledger.merge_gate("clerk", 7)).exit_class == 3
        assert not port.calls
    finally:
        await ledger.stop()
        await store.close()


@pytest.mark.parametrize("problem", [None, "cap", "retarget", "main_moved", "pr_moved", "no_base"])
async def test_compare_uses_full_immutable_shas_and_refuses_incomplete_evidence(
    problem: str | None,
) -> None:
    requests: list[str] = []
    now = format_time(FixedClock().now())
    pull = PullRequest(REPO, 7, "work", HEAD, REPO, "open", now, now, True, 1)

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        requests.append(path)
        if path.startswith("/compare/"):
            assert request.url.params["page"] == "1"
            left, _, right = path.removeprefix("/compare/").partition("...")
            assert (left, right) in {(HEAD, TIP), (BASE, HEAD), (BASE, TIP)}
            count = 300 if problem == "cap" and (left, right) == (BASE, HEAD) else 1
            repo_file = {"filename": "both.txt", "status": "modified"}
            return httpx.Response(
                200,
                json={
                    "base_commit": {"sha": left},
                    "merge_base_commit": {"sha": None if problem == "no_base" else BASE},
                    "status": "ahead" if left == BASE else "diverged",
                    "files": [repo_file] * count,
                },
            )
        if path == "/pulls/7":
            return httpx.Response(
                200,
                json={
                    "state": "open",
                    "updated_at": now,
                    "head": {
                        "sha": NEXT if problem == "pr_moved" else HEAD,
                        "repo": {"full_name": "Example/DEMO"},
                        "ref": "work",
                    },
                    "base": {
                        "ref": "next" if problem == "retarget" else "main",
                        "repo": {"full_name": "EXAMPLE/Demo"},
                    },
                },
            )
        assert path == "/branches/main"
        return httpx.Response(
            200, json={"commit": {"sha": NEXT if problem == "main_moved" else TIP}}
        )

    api = GitHubReadClient(API, REPO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        if problem in {"cap", "no_base"}:
            with pytest.raises(ValueError, match=r"incomplete comparison files|expected a string"):
                await fetch_pull_comparison(api, client, "fixture", pull, "main", TIP)
            return
        comparison = await fetch_pull_comparison(api, client, "fixture", pull, "main", TIP)
    assert comparison.complete == (problem is None)
    assert requests[:3] == [
        f"/compare/{HEAD}...{TIP}",
        f"/compare/{BASE}...{HEAD}",
        f"/compare/{BASE}...{TIP}",
    ]
    if problem is None:
        assert (
            comparison.attributes["pull_paths"]
            == comparison.attributes["main_paths"]
            == ["both.txt"]
        )


def test_fixture_write_surface() -> None:
    assert {name for name in FixtureGitHubWrite.__dict__ if not name.startswith("_")} == {
        "update_branch",
        "merge",
    }
