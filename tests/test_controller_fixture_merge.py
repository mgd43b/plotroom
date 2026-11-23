"""The controller opt-in uses only fresh authoritative fixture writes."""

from dataclasses import replace
from pathlib import Path

import pytest

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.config import ConfigError, load_config
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.branch_updates import BRANCH_UPDATED
from plotroom.core.catalog import build_catalog
from plotroom.core.github_projection import GitHubRecord
from plotroom.core.ledger import Ledger
from plotroom.core.merge_controller import MERGE_OBSERVED, MERGE_READY, MERGE_STAGED
from plotroom.core.merge_gate import GateSnapshot
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.github_write import BranchUpdateReceipt, FixtureMergeEvidence
from test_branch_updates import HEAD, NEXT, POLICY, REPO, TIP, FixtureWrite, observe

MERGED = "8" * 40
ACTION_KEY = f"controller_merge:7:{HEAD}:{TIP}"


class MergeFixture(FixtureWrite):
    def __init__(self) -> None:
        super().__init__()
        self.snapshot: GateSnapshot | None = None
        self.reads = 0
        self.applied = False
        self.lose_merge_response = False
        self.move_at_write = False
        self.merge_calls: list[tuple[str, str, str, str]] = []

    async def merge_evidence(
        self, *, repository: str, number: int, action_key: str
    ) -> FixtureMergeEvidence:
        assert (repository, number, action_key) == (REPO, 7, ACTION_KEY)
        self.reads += 1
        return FixtureMergeEvidence(self.snapshot, "fixture-revision", self.applied)

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
        assert (repository, number, action_key) == (REPO, 7, ACTION_KEY)
        self.merge_calls.append((expected_head_sha, expected_main_sha, method, evidence_revision))
        if self.move_at_write:
            return False
        assert not self.applied
        self.applied = True
        if self.lose_merge_response:
            raise TimeoutError("lost response after the fixture applied")
        return True


async def runtime_for(
    directory: Path, clock: FixedClock, fixture: MergeFixture | None, *, on: bool
) -> tuple[SqliteStore, Ledger, ProjectRuntime]:
    store = SqliteStore("demo", directory)
    await store.migrate()
    ledger = Ledger(
        store,
        build_catalog(frozenset({"worker"}), github_repository=REPO),
        clock,
        github_repository=REPO,
        merge_gate_policy=replace(POLICY, controller_merges=on, merge_method="squash"),
        live_merge_controller=True,
        fixture_merge_enabled=fixture is not None,
    )
    ledger.start()
    await ledger.set_github_error(None)
    return (
        store,
        ledger,
        ProjectRuntime(
            ledger, clock, tick_seconds=5, action_attempts=1, fixture_github_merge=fixture
        ),
    )


@pytest.mark.parametrize("with_fixture", [False, True])
async def test_off_or_no_injected_adapter_means_no_write(
    tmp_path: Path, with_fixture: bool
) -> None:
    fixture = MergeFixture() if with_fixture else None
    # Opt-in without the adapter and an injected adapter without opt-in both stay read-only.
    store, ledger, runtime = await runtime_for(tmp_path, FixedClock(), fixture, on=not with_fixture)
    try:
        await observe(ledger, overlap=False)
        _, items = await store.read_next("orchestrator", 0, 5)
        assert [item.kind for item in items] == [MERGE_READY]
        assert not await runtime.run_one_action()
        assert all(event.kind != MERGE_STAGED for event in await ledger.events_after(0))
        if fixture is not None:
            assert fixture.reads == 0
            assert fixture.merge_calls == []
    finally:
        await ledger.stop()
        await store.close()


async def test_fresh_pins_revision_and_lost_response_recovery(tmp_path: Path) -> None:
    fixture, clock = MergeFixture(), FixedClock()
    store, ledger, runtime = await runtime_for(tmp_path, clock, fixture, on=True)
    try:
        await observe(ledger, overlap=False)
        ready = await store.read_merge_gate(REPO, 7)
        assert ready.threads is not None
        fixture.snapshot = replace(
            ready,
            threads=replace(
                ready.threads,
                records=(
                    GitHubRecord(
                        "new",
                        "review_thread",
                        {"id": "new", "number": 7, "resolved": False},
                    ),
                ),
            ),
        )
        # Same refs and still-recent local facts are insufficient when the server has a new thread.
        assert not await runtime.run_one_action()
        assert not fixture.merge_calls

        await ledger.execute("orchestrator", "freeze", "freeze", {"reason": "fixture window"})
        fixture.snapshot = ready
        assert await runtime.run_one_action()  # retire the pre-freeze offer
        assert not fixture.merge_calls
        await ledger.execute("orchestrator", "unfreeze", "unfreeze", {})

        fixture.move_at_write = True
        assert not await runtime.run_one_action()
        assert not fixture.applied
        fixture.move_at_write = False
        fixture.lose_merge_response = True
        assert await runtime.run_one_action()
        assert fixture.applied
        assert fixture.merge_calls == [(HEAD, TIP, "squash", "fixture-revision")] * 2
    finally:
        await ledger.stop()
        await store.close()

    store, ledger, runtime = await runtime_for(tmp_path, clock, fixture, on=True)
    try:
        assert await runtime.run_one_action()  # recover the receipt with the action at its limit
        assert not await runtime.run_one_action()
        assert len(fixture.merge_calls) == 2
        pull = await ledger.pull_request(REPO, 7)
        assert pull is not None
        await ledger.run(
            "system",
            lambda uow: uow.save_pull_request(
                replace(pull, state="merged", merge_commit_sha=MERGED)
            ),
        )
        merged = [event for event in await ledger.events_after(0) if event.kind == MERGE_OBSERVED]
        assert [(e.fields["classification"], e.fields["merge_sha"]) for e in merged] == [
            ("gated", MERGED)
        ]
        assert await store.pending_action() is None
    finally:
        await ledger.stop()
        await store.close()


@pytest.mark.parametrize("value", ['"true"', "1", "[]"])
def test_controller_opt_in_requires_a_toml_boolean(tmp_path: Path, value: str) -> None:
    file = tmp_path / "config.toml"
    file.write_text(
        f'[projects.demo.github]\nrepository = "{REPO}"\ncontroller_merges = {value}\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="controller_merges must be a Boolean"):
        load_config(env={}, path=file)


async def test_duplicate_items_retire_and_draft_skips_gating(tmp_path: Path) -> None:
    store, ledger, _ = await runtime_for(tmp_path, FixedClock(), None, on=False)
    try:
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "lane-start",
            {
                "lane": "feature",
                "worker": "worker",
                "branch": "feature/default",
                "branch_pattern": "work*",
                "work_items": [],
            },
        )
        await observe(ledger, overlap=False)
        pull = await ledger.pull_request(REPO, 7)
        assert pull is not None
        first = replace(pull, number=6, branch="work-original", head_sha="6" * 40)
        await ledger.run("system", lambda uow: uow.save_pull_request(first))

        _, items = await store.read_next("orchestrator", 0, 50)
        [duplicate] = [item for item in items if item.kind == "duplicate_lane_pr"]
        assert duplicate.context["first_pr"] == first.number
        assert duplicate.context["number"] == pull.number
        assert all(item.kind != MERGE_READY for item in items)
        await ledger.refresh_merge_controller()
        _, repeated = await store.read_next("orchestrator", 0, 50)
        assert [item.id for item in repeated if item.kind == "duplicate_lane_pr"] == [duplicate.id]

        async def retire(uow: UnitOfWork) -> None:
            await uow.save_pull_request(replace(first, state="closed"))
            await uow.save_pull_request(replace(pull, draft=True))

        await ledger.run("system", retire)
        _, remaining = await store.read_next("orchestrator", 0, 50)
        assert remaining == []
        assert (await ledger.merge_gate("orchestrator", 7)).reasons[0].code == "draft_pr"

        await ledger.run("system", lambda uow: uow.save_pull_request(pull))
        _, restored = await store.read_next("orchestrator", 0, 50)
        assert [item.kind for item in restored] == [MERGE_READY]
    finally:
        await ledger.stop()
        await store.close()


async def test_branch_updated_logs_successful_receipt_once(tmp_path: Path) -> None:
    class BranchFixture(FixtureWrite):
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
            assert (repository, number, expected_head_sha, expected_main_sha) == (
                REPO,
                7,
                HEAD,
                TIP,
            )
            assert not recovery_only
            return BranchUpdateReceipt("updated", TIP)

    store, ledger, _ = await runtime_for(tmp_path, FixedClock(), None, on=False)
    runtime = ProjectRuntime(
        ledger,
        FixedClock(),
        tick_seconds=5,
        action_attempts=1,
        fixture_github_write=BranchFixture(),
    )
    try:
        await observe(ledger, overlap=True)
        assert await runtime.stage_branch_update(7)
        assert await runtime.run_one_action()
        assert not await runtime.stage_branch_update(7)
        assert not await runtime.run_one_action()
        [logged] = [event for event in await ledger.events_after(0) if event.kind == BRANCH_UPDATED]
        assert logged.body == "BRANCH UPDATED #7"
        assert (logged.fields["head_sha"], logged.fields["main_sha"]) == (HEAD, TIP)
    finally:
        await ledger.stop()
        await store.close()


@pytest.mark.parametrize("intervening_pass", [False, True])
async def test_refusal_follows_lane_binding_without_duplicate_log(
    tmp_path: Path, intervening_pass: bool
) -> None:
    store, ledger, _ = await runtime_for(tmp_path, FixedClock(), None, on=False)
    try:
        await observe(ledger, unresolved=True)
        _, before = await store.read_next("orchestrator", 0, 50)
        assert [item.role for item in before if item.kind == "merge_refusal"] == ["orchestrator"]
        if intervening_pass:
            await observe(ledger, overlap=False)
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "bind-ownership",
            {"lane": "feature", "worker": "worker", "branch": "work", "work_items": []},
        )
        if intervening_pass:
            await observe(ledger, unresolved=True)
        _, owned = await store.read_next("worker:worker", 0, 50)
        assert [item.context["number"] for item in owned if item.kind == "merge_refusal"] == [7]
        receipts = [
            event
            for event in await ledger.events_after(0)
            if event.kind == "core.merge_refusal_reported"
        ]
        assert len(receipts) == 1
    finally:
        await ledger.stop()
        await store.close()


async def test_delayed_merge_sha_preserves_gated_observation(tmp_path: Path) -> None:
    store, ledger, _ = await runtime_for(tmp_path, FixedClock(), None, on=False)
    try:
        await observe(ledger, overlap=False)
        pull = await ledger.pull_request(REPO, 7)
        assert pull is not None
        merged = replace(pull, state="merged")
        await ledger.run("system", lambda uow: uow.save_pull_request(merged))
        assert (await store.read_next("orchestrator", 0, 50))[1] == []
        assert not [e for e in await ledger.events_after(0) if e.kind == MERGE_OBSERVED]
        await ledger.run(
            "system",
            lambda uow: uow.save_pull_request(replace(merged, merge_commit_sha=MERGED)),
        )
        [event] = [e for e in await ledger.events_after(0) if e.kind == MERGE_OBSERVED]
        assert (event.fields["classification"], event.fields["merge_sha"]) == ("gated", MERGED)
    finally:
        await ledger.stop()
        await store.close()


async def test_new_occurrence_replaces_retired_fixture_action(tmp_path: Path) -> None:
    fixture = MergeFixture()
    store, ledger, runtime = await runtime_for(tmp_path, FixedClock(), fixture, on=True)
    try:
        await observe(ledger, overlap=False)
        fixture.snapshot = await store.read_merge_gate(REPO, 7)
        await ledger.execute("orchestrator", "freeze", "freeze", {"reason": "fixture freeze"})
        assert await runtime.run_one_action()
        assert not fixture.merge_calls
        assert await store.pending_action() is None

        await ledger.execute("orchestrator", "unfreeze", "unfreeze", {})
        assert await runtime.run_one_action()
        assert fixture.merge_calls == [(HEAD, TIP, "squash", "fixture-revision")]
        assert not await runtime.run_one_action()
    finally:
        await ledger.stop()
        await store.close()


async def test_merge_sha_backfill_is_quiet_for_populated_projection(tmp_path: Path) -> None:
    store, ledger, _ = await runtime_for(tmp_path, FixedClock(), None, on=False)
    try:
        await observe(ledger, overlap=False)
        pull = await ledger.pull_request(REPO, 7)
        assert pull is not None
        historical = replace(pull, number=6, state="merged")
        async with store.transaction() as tx:
            await tx.save_pull_request(historical)
        await ledger.run(
            "system",
            lambda uow: uow.save_pull_request(replace(historical, merge_commit_sha=MERGED)),
        )
        assert not [e for e in await ledger.events_after(0) if e.kind == MERGE_OBSERVED]
    finally:
        await ledger.stop()
        await store.close()


async def test_main_pin_replaces_offer_without_duplicate_verdict(tmp_path: Path) -> None:
    store, ledger, _ = await runtime_for(tmp_path, FixedClock(), None, on=False)
    try:
        await observe(ledger, overlap=False)
        _, [offered] = await store.read_next("orchestrator", 0, 50)
        assert offered.context["main_sha"] == TIP
        await observe(ledger, overlap=False, main=NEXT)
        _, [repinned] = await store.read_next("orchestrator", 0, 50)
        assert repinned.id != offered.id
        assert repinned.context["head_sha"] == offered.context["head_sha"] == HEAD
        assert repinned.context["main_sha"] == NEXT
        _, retired = await store.first_item_change("orchestrator", offered.seq)
        assert retired is not None
        assert retired["id"] == offered.id
        assert (retired["state"], retired["context"]["main_sha"]) == ("closed", TIP)
        _, replacement = await store.first_item_change("orchestrator", retired["seq"])
        assert replacement is not None
        assert replacement["id"] == repinned.id
        assert replacement["context"]["main_sha"] == NEXT
        verdicts = [e for e in await ledger.events_after(0) if e.kind == "core.merge_verdict"]
        assert [e.fields["verdict"] for e in verdicts] == ["MERGE"]
    finally:
        await ledger.stop()
        await store.close()
