"""Deploy-window command permissions and durable, per-project gate behavior."""

from pathlib import Path

import pytest

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError, format_time
from plotroom.core.github import PullRequest
from plotroom.core.github_projection import (
    CHECKS,
    MAIN,
    MAIN_CI,
    PULL_TEXT,
    THREADS,
    GitHubRecord,
    GitHubResource,
)
from plotroom.core.ledger import Ledger
from plotroom.core.merge_gate import GatePolicy
from plotroom.core.merge_overlap import PULL_COMPARISON
from plotroom.core.snapshot import render_snapshot

REPOSITORY = "fixture/project"
REASON = "  maintenance window\n"
MAIN_HEAD = "a" * 40


def project(store: SqliteStore, clock: FixedClock, repository: str | None = REPOSITORY) -> Ledger:
    ledger = Ledger(
        store,
        build_catalog(frozenset({"fixture"}), github_repository=repository),
        clock,
        github_repository=repository,
        merge_gate_policy=GatePolicy(1, ci_workflow="ci.yml"),
    )
    ledger.github_error = None
    ledger.start()
    return ledger


async def test_freeze_persists_and_unfreeze_restores_each_verdict(tmp_path: Path) -> None:
    clock = FixedClock()
    observed_at = format_time(clock.now())
    store = SqliteStore("fixture", tmp_path)
    await store.migrate()
    ledger = project(store, clock)
    try:
        async with store.transaction() as tx:
            main = GitHubResource(
                (),
                attributes={"branch": "main", "head_sha": MAIN_HEAD},
                as_of=observed_at,
            )
            job = GitHubRecord(
                "1/1",
                "workflow_run",
                {
                    "id": 1,
                    "status": "completed",
                    "head_sha": MAIN_HEAD,
                    "conclusion": "success",
                },
            )
            main_ci = GitHubResource(
                (job,),
                attributes={**main.attributes, "workflow": "ci.yml"},
                as_of=observed_at,
            )
            await tx.save_github_resource(REPOSITORY, MAIN, "", main)
            await tx.save_github_resource(REPOSITORY, MAIN_CI, "", main_ci)
            for number, conclusion in ((1, "success"), (2, "failure")):
                head = str(number) * 40
                await tx.save_pull_request(
                    PullRequest(
                        REPOSITORY,
                        number,
                        f"branch-{number}",
                        head,
                        REPOSITORY,
                        "open",
                        observed_at,
                        observed_at,
                        True,
                        1,
                    )
                )
                fields = {
                    "id": number,
                    "name": "tests",
                    "head_sha": head,
                    "status": "completed",
                    "conclusion": conclusion,
                }
                checks = GitHubResource(
                    (GitHubRecord(str(number), "check_run", fields),),
                    as_of=observed_at,
                )
                await tx.save_github_resource(REPOSITORY, CHECKS, head, checks)
                pin = {"head_sha": head, "pull_version": 1, "pull_updated_at": observed_at}
                await tx.save_github_resource(
                    REPOSITORY,
                    THREADS,
                    str(number),
                    GitHubResource((), attributes=pin, as_of=observed_at),
                )
                await tx.save_github_resource(
                    REPOSITORY,
                    PULL_TEXT,
                    str(number),
                    GitHubResource(
                        (),
                        attributes={
                            **pin,
                            "body": "",
                            "commit_count": 1,
                            "commit_messages": ["Update fixture branch"],
                        },
                        as_of=observed_at,
                    ),
                )
                await tx.save_github_resource(
                    REPOSITORY,
                    PULL_COMPARISON,
                    str(number),
                    GitHubResource(
                        (),
                        attributes={
                            **pin,
                            "branch": "main",
                            "main_tip": MAIN_HEAD,
                            "merge_base": "b" * 40,
                            "pull_paths": ["feature.py"],
                            "main_paths": ["settings.py"],
                        },
                        as_of=observed_at,
                    ),
                )

        baseline = [await ledger.merge_gate("clerk", pr) for pr in (1, 2)]
        assert baseline[0].verdict == "MERGE"
        assert baseline[1].reasons[0].code == "checks_failing"
        frozen = await ledger.execute("orchestrator", "freeze", "one", {"reason": REASON})
        assert frozen.result == {"frozen": True, "reason": REASON}
    finally:
        await ledger.stop()
        await store.close()

    store = SqliteStore("fixture", tmp_path)
    ledger = project(store, clock)
    other = SqliteStore("elsewhere", tmp_path)
    await other.migrate()
    separate = project(other, clock, repository=None)
    try:
        for pr, previous in enumerate(baseline, start=1):
            decision = await ledger.merge_gate("clerk", pr)
            assert (decision.verdict, decision.exit_class) == ("REFUSE", 2)
            assert (decision.head_sha, decision.as_of) == (previous.head_sha, previous.as_of)
            [reason] = decision.reasons
            assert (reason.code, reason.detail) == ("deploy_window", REASON)
        _, _, _, _, reason_from_board = await ledger.snapshot_summary("orchestrator")
        assert reason_from_board == REASON
        assert (await separate.merge_gate("orchestrator", 1)).reasons[0].code != "deploy_window"
        cleared = await ledger.execute("orchestrator", "unfreeze", "clear", {})
        assert cleared.result == {"frozen": False, "reason": None}
        replay = await ledger.execute("orchestrator", "freeze", "one", {"reason": REASON})
        assert replay.replayed
        assert [await ledger.merge_gate("orchestrator", pr) for pr in (1, 2)] == baseline
        assert [event.kind for event in await ledger.events_after(0)] == [
            "core.freeze",
            "core.unfreeze",
        ]
        assert await ledger.pending_action() is None
    finally:
        await separate.stop()
        await other.close()
        await ledger.stop()
        await store.close()


def test_snapshot_keeps_freeze_visible_when_reason_exceeds_budget() -> None:
    board = render_snapshot("fixture", 1, [], {}, 256, freeze_reason="long reason " * 500)
    assert board["freezes"] == {"deploy": {"reason_omitted": True}}


async def test_only_orchestrator_can_change_window_without_github(tmp_path: Path) -> None:
    store = SqliteStore("fixture", tmp_path)
    await store.migrate()
    ledger = project(store, FixedClock(), repository=None)
    try:
        for role in ("clerk", "worker:fixture"):
            for command, arguments in (("freeze", {"reason": "fixture"}), ("unfreeze", {})):
                with pytest.raises(LedgerError) as denied:
                    await ledger.execute(role, command, f"{role}-{command}", arguments)
                assert denied.value.code == "forbidden"
        with pytest.raises(LedgerError, match="must not be blank"):
            await ledger.execute("orchestrator", "freeze", "blank", {"reason": " \n"})
        assert await ledger.head_seq() == 0

        await ledger.execute("orchestrator", "freeze", "window", {"reason": REASON})
        decision = await ledger.merge_gate("orchestrator", pr=4)
        assert decision.reasons[0].code == "deploy_window"
        assert decision.reasons[0].detail == REASON
        assert decision.head_sha is None
        assert decision.as_of is None
    finally:
        await ledger.stop()
        await store.close()
