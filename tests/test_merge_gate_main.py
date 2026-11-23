"""Clerk Rule 3 over the configured workflow and current label provenance."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from fakes import FixedClock
from plotroom.adapters.github import GitHubUnavailable, LocalGitHubCredential
from plotroom.adapters.github.ci import fetch_main_ci
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.pr_details import fetch_labels
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.config import ConfigError, load_config
from plotroom.core import merge_gate
from plotroom.core.catalog import build_catalog
from plotroom.core.events import NewEvent
from plotroom.core.github_projection import LABELS, MAIN, MAIN_CI, GitHubRecord, GitHubResource
from plotroom.core.ledger import Ledger
from plotroom.core.merge_flakes import MAIN_FLAKE_EVENT
from plotroom.core.merge_gate import GatePolicy, GateSnapshot, determine_main_verdict, evaluate_gate
from test_github_details import API, MAIN_SHA, NOW, REPO, Fixture, status
from test_merge_gate_rule2 import CHECK, EMPTY_THREADS, PULL

WORKFLOW = "ci.yml"
FIX = "main-fix"
CHECKED_AT = datetime.fromisoformat(NOW)
POLICY = GatePolicy(1, ci_workflow=WORKFLOW, fix_label=FIX, authorized_label_logins=("editor",))
OTHER_SHA = "f" * 40
BRANCH = "trunk"


def run(identifier: int, conclusion: str | None, sha: str = MAIN_SHA) -> dict[str, Any]:
    return {
        **status(identifier, "Python CI", conclusion),
        "head_sha": sha,
        "head_branch": BRANCH,
        "run_attempt": 1,
        "created_at": NOW,
        "workflow_id": 123,
        "event": "push",
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
    }


def record(
    identifier: int,
    conclusion: str,
    *jobs: dict[str, Any],
    sha: str = MAIN_SHA,
    jobs_complete: bool = True,
) -> GitHubRecord:
    return GitHubRecord(
        f"{identifier}/1",
        "workflow_run",
        {**run(identifier, conclusion, sha), "jobs": list(jobs)},
        details={"workflow_id": 123, "jobs_complete": jobs_complete},
    )


def main(*runs: GitHubRecord) -> GitHubResource:
    return GitHubResource(
        runs, as_of=NOW, attributes={"branch": BRANCH, "head_sha": MAIN_SHA, "workflow": WORKFLOW}
    )


def job(identifier: int, conclusion: str) -> dict[str, Any]:
    return {
        **status(identifier + 100, f"test {identifier}", conclusion),
        "check_run_id": identifier,
    }


@pytest.mark.parametrize(
    ("history", "proven", "expected"),
    [
        ((record(4, "success", jobs_complete=False),), (), ("green", "success " + MAIN_SHA[:8])),
        ((record(4, "startup_failure"),), (), ("red", "startup_failure " + MAIN_SHA[:8])),
        ((record(4, "failure", job(31, "timed_out")),), (), ("red", "failure " + MAIN_SHA[:8])),
        (
            (record(4, "failure", jobs_complete=False), record(3, "success")),
            (),
            ("unreadable", f"the jobs of run 4 could not be read ({MAIN_SHA[:8]})"),
        ),
        (
            (record(4, "failure", status(31, "pending test", None)), record(3, "success")),
            (),
            ("unreadable", f"the jobs of run 4 could not be read ({MAIN_SHA[:8]})"),
        ),
        (
            (record(4, "failure", job(31, "unknown")), record(3, "success")),
            (),
            ("unreadable", f"the jobs of run 4 could not be read ({MAIN_SHA[:8]})"),
        ),
        (
            (
                record(6, "cancelled"),
                record(5, "skipped"),
                record(4, "failure", job(31, "cancelled")),
                record(3, "success", sha=OTHER_SHA),
            ),
            (),
            ("green", "success " + OTHER_SHA[:8]),
        ),
        ((record(4, "failure"),), (), ("none", "no completed CI run with a verdict")),
        (
            (record(4, "failure", job(31, "failure")), record(3, "success", sha=OTHER_SHA)),
            ((MAIN_SHA, 31),),
            ("green", "success " + OTHER_SHA[:8]),
        ),
        (
            (record(4, "failure", job(31, "failure"), job(32, "timed_out")), record(3, "success")),
            ((MAIN_SHA, 31), (OTHER_SHA, 32)),
            ("red", "failure " + MAIN_SHA[:8]),
        ),
        (
            (record(4, "failure", status(31, "missing check link", "failure")),),
            ((MAIN_SHA, 31),),
            ("red", "failure " + MAIN_SHA[:8]),
        ),
        (
            (*(record(number, "cancelled") for number in range(31, 1, -1)), record(1, "success")),
            (),
            ("none", "no completed CI run with a verdict"),
        ),
    ],
)
def test_main_verdict_matches_clerk_window_and_complete_flake_evidence(
    history: tuple[GitHubRecord, ...],
    proven: tuple[tuple[str, int], ...],
    expected: tuple[str, str],
) -> None:
    snapshot = GateSnapshot(None, None, main_ci=main(*history), main_flakes=frozenset(proven))
    verdict = determine_main_verdict(snapshot, POLICY, CHECKED_AT)
    assert (verdict.kind, verdict.detail, verdict.branch, verdict.head_sha) == (
        *expected,
        BRANCH,
        MAIN_SHA,
    )


@pytest.mark.parametrize(
    "history",
    [
        None,
        replace(main(record(1, "success")), complete=False),
        replace(main(record(1, "success")), attributes={"workflow": "unrelated.yml"}),
        replace(main(record(1, "success")), as_of="2026-11-22T11:50:00Z"),
    ],
)
def test_incomplete_stale_or_other_workflow_does_not_clear_main(
    history: GitHubResource | None,
) -> None:
    result = determine_main_verdict(GateSnapshot(None, None, main_ci=history), POLICY, CHECKED_AT)
    assert (result.kind, result.detail) == ("unreadable", "could not list the CI runs")


@pytest.mark.parametrize(
    ("login", "actor_type", "active", "expected"),
    [
        ("Editor", "User", True, 42),
        ("builder", "User", True, None),
        ("editor", "Bot", True, None),
        ("editor", None, True, None),
        ("editor", "User", False, None),
    ],
)
async def test_only_a_current_configured_human_label_can_waive_red(
    monkeypatch: pytest.MonkeyPatch,
    login: str,
    actor_type: str | None,
    active: bool,
    expected: int | None,
) -> None:
    monkeypatch.setattr(merge_gate, "GATE_RULES", merge_gate.GATE_RULES[:3])

    def respond(request: httpx.Request) -> httpx.Response:
        def event(identifier: int, action: str, actor: dict[str, Any]) -> dict[str, Any]:
            return {"id": identifier, "event": action, "actor": actor, "label": {"name": FIX}}

        prefix = f"/repos/{REPO}/issues/4/"
        path = request.url.path.removeprefix(prefix)
        assert request.method == "GET"
        if request.url.path.endswith("/pulls/4"):
            return httpx.Response(
                200, json={"head": {"sha": PULL.head_sha}, "updated_at": PULL.updated_at}
            )
        if path == "labels":
            return httpx.Response(200, json=[{"name": FIX}] if active else [])
        assert path == "events"
        old_actor = {"login": "editor", "type": "User"}
        return httpx.Response(
            200,
            json=[
                event(40, "labeled", old_actor),
                event(41, "unlabeled", old_actor),
                event(42, "labeled", {"login": login, "type": actor_type}),
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        labels = await fetch_labels(GitHubReadClient(API, REPO), client, "fixture", PULL)
    labels = replace(labels, as_of=NOW)
    snapshot = GateSnapshot(
        PULL,
        GitHubResource((CHECK,), as_of=NOW),
        threads=EMPTY_THREADS,
        labels=labels,
        main_ci=main(record(3, "startup_failure")),
    )
    assert merge_gate.authorized_label(snapshot, POLICY, CHECKED_AT, FIX) == expected
    decision = evaluate_gate(snapshot, POLICY, CHECKED_AT)
    assert (decision.verdict, decision.exit_class) == (("MERGE", 0) if expected else ("REFUSE", 5))
    if expected is None:
        assert decision.reasons[0].code == "main_red"
        return

    prior_projection = replace(labels, attributes={"labels": {FIX: login}})
    for stale in (
        replace(snapshot, labels=prior_projection),
        replace(snapshot, pull=replace(PULL, updated_at="2026-11-22T12:00:01Z")),
        replace(snapshot, labels=replace(labels, complete=False)),
    ):
        assert evaluate_gate(stale, POLICY, CHECKED_AT).reasons[0].code == "main_red"

    for blocked, code in (
        (replace(snapshot, checks=GitHubResource((), as_of=NOW)), "checks_below_minimum"),
        (
            replace(
                snapshot,
                threads=replace(
                    EMPTY_THREADS,
                    records=(
                        GitHubRecord(
                            "thread",
                            "review_thread",
                            {"id": "thread", "number": PULL.number, "resolved": False},
                        ),
                    ),
                ),
            ),
            "review_threads_unresolved",
        ),
        (replace(snapshot, main_ci=main()), "main_no_verdict"),
        (
            replace(snapshot, main_ci=main(record(3, "failure", jobs_complete=False))),
            "main_no_verdict",
        ),
    ):
        assert evaluate_gate(blocked, POLICY, CHECKED_AT).reasons[0].code == code


async def test_label_provenance_reads_past_a_full_page_without_link() -> None:
    pages: list[int] = []

    def label_event(identifier: int, action: str, login: str) -> dict[str, Any]:
        return {
            "id": identifier,
            "event": action,
            "actor": {"login": login, "type": "User"},
            "label": {"name": FIX},
        }

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path.endswith("/pulls/4"):
            return httpx.Response(
                200, json={"head": {"sha": PULL.head_sha}, "updated_at": PULL.updated_at}
            )
        if request.url.path.endswith("/labels"):
            return httpx.Response(200, json=[{"name": FIX}])
        assert request.url.path.endswith("/events")
        page = int(request.url.params["page"])
        pages.append(page)
        if page == 1:
            return httpx.Response(
                200,
                json=[
                    label_event(1, "labeled", "editor"),
                    *({"id": identifier, "event": "assigned"} for identifier in range(2, 101)),
                ],
            )
        assert page == 2
        return httpx.Response(
            200,
            json=[label_event(101, "unlabeled", "editor"), label_event(102, "labeled", "builder")],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        labels = await fetch_labels(GitHubReadClient(API, REPO), client, "fixture", PULL)
    assert pages == [1, 2]
    assert labels.attributes["labels"] == {FIX: "builder"}
    snapshot = GateSnapshot(PULL, None, labels=replace(labels, as_of=NOW))
    assert merge_gate.authorized_label(snapshot, POLICY, CHECKED_AT, FIX) is None


async def test_label_evidence_refuses_when_the_pr_changes_during_fetch() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/labels"):
            return httpx.Response(200, json=[{"name": FIX}])
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "event": "labeled",
                        "actor": {"login": "editor", "type": "User"},
                        "label": {"name": FIX},
                    }
                ],
            )
        assert request.url.path.endswith("/pulls/4")
        return httpx.Response(
            200, json={"head": {"sha": PULL.head_sha}, "updated_at": "2026-11-24T00:00:00Z"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match="PR changed during label poll"):
            await fetch_labels(GitHubReadClient(API, REPO), client, "fixture", PULL)


@pytest.mark.parametrize("incomplete", [False, True])
@pytest.mark.parametrize(
    ("conclusion", "verdict_kind"), [("success", "green"), ("startup_failure", "red")]
)
async def test_provider_workflow_filter_and_failed_jobs_count(
    incomplete: bool, conclusion: str, verdict_kind: str
) -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        calls.append(path)
        assert request.method == "GET"
        if path == "":
            return httpx.Response(200, json={"default_branch": BRANCH})
        if path == f"/branches/{BRANCH}":
            return httpx.Response(200, json={"commit": {"sha": MAIN_SHA}})
        if path == f"/actions/workflows/{WORKFLOW}/runs":
            assert dict(request.url.params) == {
                "branch": BRANCH,
                "page": "1",
                "per_page": "100",
            }
            return httpx.Response(
                200,
                json={
                    "total_count": 3,
                    "workflow_runs": [run(4, "failure"), run(3, conclusion), run(2, "failure")],
                },
            )
        if path == "/actions/runs/4/attempts/1/jobs":
            return httpx.Response(200, json={"total_count": int(incomplete), "jobs": []})
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        api = GitHubReadClient(API, REPO)
        observed = await fetch_main_ci(api, client, "fixture", WORKFLOW, None, None)
    assert "/actions/runs" not in calls
    assert [entry.fields["id"] for entry in observed.records] == [4, 3]
    assert [path for path in calls if path.endswith("/jobs")] == ["/actions/runs/4/attempts/1/jobs"]
    assert observed.records[0].details["jobs_complete"] is not incomplete
    snapshot = GateSnapshot(None, None, main_ci=replace(observed, as_of=NOW))
    verdict = determine_main_verdict(snapshot, POLICY, CHECKED_AT)
    assert verdict.kind == ("unreadable" if incomplete else verdict_kind)


@pytest.mark.parametrize("ambiguous", [False, True])
async def test_configured_ci_requires_positive_default_branch_attribution(ambiguous: bool) -> None:
    fork = {**run(15, "success"), "head_repository": {"full_name": "fork/repo"}}
    same_repo_pr = {**run(14, "success"), "event": "pull_request_target"}
    candidate = run(13, "startup_failure")
    if ambiguous:
        del candidate["head_repository"]

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith(f"/actions/workflows/{WORKFLOW}/runs")
        runs = [fork, same_repo_pr, candidate, run(12, "success")]
        return httpx.Response(200, json={"total_count": len(runs), "workflow_runs": runs})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        api = GitHubReadClient(API, REPO)
        if ambiguous:
            with pytest.raises(ValueError, match="establish the workflow run's repository"):
                await fetch_main_ci(api, client, "fixture", WORKFLOW, None, main())
            return
        observed = await fetch_main_ci(api, client, "fixture", WORKFLOW, None, main())
    assert [entry.fields["id"] for entry in observed.records] == [13]
    snapshot = GateSnapshot(None, None, main_ci=replace(observed, as_of=NOW))
    assert determine_main_verdict(snapshot, POLICY, CHECKED_AT).kind == "red"


@pytest.mark.parametrize("prior_fault", [None, "missing", "different_sha", "different_attempt"])
async def test_pending_rerun_preserves_the_exact_previous_verdict(prior_fault: str | None) -> None:
    paths: list[str] = []
    earlier = run(4, "failure")
    if prior_fault == "different_sha":
        earlier["head_sha"] = OTHER_SHA
    if prior_fault == "different_attempt":
        earlier["run_attempt"] = 2

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        paths.append(path)
        assert request.method == "GET"
        if path == f"/actions/workflows/{WORKFLOW}/runs":
            runs = [run(5, None), {**run(4, None), "run_attempt": 2}, run(3, "success")]
            return httpx.Response(200, json={"total_count": len(runs), "workflow_runs": runs})
        if path == "/actions/runs/4/attempts/1":
            return (
                httpx.Response(404)
                if prior_fault == "missing"
                else httpx.Response(200, json=earlier)
            )
        assert path == "/actions/runs/4/attempts/1/jobs"
        return httpx.Response(200, json={"total_count": 1, "jobs": [status(41, "lint", "failure")]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        api = GitHubReadClient(API, REPO)
        if prior_fault == "missing":
            with pytest.raises(GitHubUnavailable):
                await fetch_main_ci(api, client, "fixture", WORKFLOW, None, main())
            return
        if prior_fault is not None:
            with pytest.raises(ValueError, match="establish the prior completed workflow attempt"):
                await fetch_main_ci(api, client, "fixture", WORKFLOW, None, main())
            return
        observed = await fetch_main_ci(api, client, "fixture", WORKFLOW, None, main())
        refreshed = await fetch_main_ci(api, client, "fixture", WORKFLOW, observed, main())
    assert observed == refreshed
    assert [entry.key for entry in observed.records] == ["4/1", "3/1"]
    assert paths.count("/actions/runs/4/attempts/1") == 1
    assert paths.count("/actions/runs/4/attempts/1/jobs") == 1
    snapshot = GateSnapshot(None, None, main_ci=replace(observed, as_of=NOW))
    assert determine_main_verdict(snapshot, POLICY, CHECKED_AT).kind == "red"


async def test_poller_preserves_monitor_while_reading_configured_history(tmp_path: Path) -> None:
    fixture = Fixture()

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        if path != f"/actions/workflows/{WORKFLOW}/runs":
            return fixture.handle(request)
        assert request.method == "GET"
        return httpx.Response(200, json={"total_count": 1, "workflow_runs": [run(2, "success")]})

    store = SqliteStore("fixture", tmp_path)
    await store.migrate()
    clock = FixedClock(CHECKED_AT)
    ledger = Ledger(store, build_catalog(github_repository=REPO), clock, github_repository=REPO)
    ledger.start()
    poller = GitHubDetailPoller(
        ledger,
        clock,
        GitHubReadClient(API, REPO),
        LocalGitHubCredential("TOKEN", {"TOKEN": "fixture"}, None),
        ("image",),
        ci_workflow=WORKFLOW,
    )
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await poller.poll(client)
            await poller.poll(client)
        recent = await ledger.github_resource(REPO, MAIN)
        assert recent is not None
        assert [entry.fields["id"] for entry in recent.records] == [50]
        assert recent.records[0].details["image_jobs"] == [status(70, "image", "success")]
        snapshot = await store.read_merge_gate(REPO, PULL.number)
        assert snapshot.main_ci is not None
        assert [entry.fields["id"] for entry in snapshot.main_ci.records] == [2]
        assert determine_main_verdict(snapshot, POLICY, CHECKED_AT).kind == "green"
        assert f"/repos/{REPO}/actions/runs/2/attempts/1/jobs" not in fixture.requests
        workflow_events = [
            event for event in await store.events_after(0) if event.kind == "github.workflow_run"
        ]
        assert [event.fields["id"] for event in workflow_events] == [50]
    finally:
        await ledger.stop()
        await store.close()


async def test_store_reads_flake_proof_for_exact_repo_head_and_check_id(tmp_path: Path) -> None:
    store = SqliteStore("fixture", tmp_path)
    await store.migrate()
    ledger = Ledger(store, build_catalog(github_repository=REPO), FixedClock(CHECKED_AT))
    ledger.start()
    try:
        async with store.transaction() as transaction:
            await transaction.save_github_resource(
                REPO,
                MAIN_CI,
                "",
                main(record(4, "failure", job(31, "failure")), record(3, "success")),
            )
            await transaction.save_github_resource(REPO, LABELS, "4", GitHubResource(()))

        async def prove(repository: str, head: str, check: int) -> None:
            await ledger.run(
                "system",
                lambda unit: unit.append(
                    NewEvent(
                        "core",
                        MAIN_FLAKE_EVENT,
                        f"{repository}:{head}:{check}",
                        {"repository": repository, "head_sha": head, "check_id": check},
                    )
                ),
            )

        await prove("other/repo", MAIN_SHA, 31)
        await prove(REPO, OTHER_SHA, 31)
        await prove(REPO, MAIN_SHA, 32)
        snapshot = await store.read_merge_gate(REPO, 4)
        assert not snapshot.main_flakes
        assert determine_main_verdict(snapshot, POLICY, CHECKED_AT).kind == "red"
        await prove(REPO, MAIN_SHA, 31)
        snapshot = await store.read_merge_gate(REPO, 4)
        assert snapshot.main_flakes == frozenset({(MAIN_SHA, 31)})
        assert determine_main_verdict(snapshot, POLICY, CHECKED_AT).kind == "green"
    finally:
        await ledger.stop()
        await store.close()


def test_project_workflow_and_label_settings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    base = f'[projects.fixture.github]\nrepository = "{REPO}"\n'
    path.write_text(
        base
        + 'ci_workflow = "ci.yml"\nfix_label = "main-fix"\nauthorized_label_logins = ["Editor"]\n',
        encoding="utf-8",
    )
    policy = load_config(path, {}).github["fixture"].merge_gate_policy
    assert (policy.ci_workflow, policy.fix_label, policy.authorized_label_logins) == (
        WORKFLOW,
        FIX,
        ("editor",),
    )
    path.write_text(base + 'ci_workflow = "../ci.yml"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="ci_workflow must be a workflow filename or positive ID"):
        load_config(path, {})
