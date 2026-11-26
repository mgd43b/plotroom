"""Exercise flake evidence through the read poller and the durable project writer."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec

import httpx
import pytest

from fakes import FixedClock
from plotroom.adapters.github import GitHubPoller, LocalGitHubCredential, PullRequestFetcher
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.catalog import build_catalog
from plotroom.core.github import PullRequest
from plotroom.core.github_projection import GitHubRecord, GitHubResource
from plotroom.core.ledger import Ledger
from plotroom.core.merge_flakes import (
    FLAKE_EVENT,
    MAIN_FLAKE_EVENT,
    MainPair,
    main_pairs,
    record_main_flakes,
    record_pull_flakes,
    record_same_head,
)
from plotroom.core.unit_of_work import UnitOfWork
from test_github_details import API, FIRST, LATER, MAIN_SHA, NOW, REPO, Fixture, status

FAILED_MAIN = "4" * 40
INTERMEDIATE = "5" * 40
COMPARE = f"/compare/{FAILED_MAIN}...{MAIN_SHA}"


def check(identifier: int, name: str, conclusion: str, sha: str, count: int) -> dict[str, Any]:
    return {
        **status(identifier, name, conclusion),
        "head_sha": sha,
        "check_suite": {"id": 7},
        "app": {"id": 8},
        "completed_at": LATER if conclusion == "success" else NOW,
        "output": {"annotations_count": count},
        "html_url": f"https://github.com/{REPO}/runs/{identifier}",
    }


def annotation(path: str, name: str) -> dict[str, Any]:
    return {"annotation_level": "failure", "path": path, "title": name, "message": "x" * 1000}


def passed_test(path: str, name: str) -> dict[str, Any]:
    return {
        "annotation_level": "notice",
        "path": path,
        "title": name,
        "message": "plotroom:test-outcome=passed",
    }


class FlakeFixture(Fixture):
    def __init__(self, fault: str = "") -> None:
        super().__init__()
        self.fault = fault
        self.late_annotation = False
        self.pr_reads = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        if path == "/pulls/4" and self.fault in {"base_retargeted", "base_advanced"}:
            response = super().handle(request)
            current: dict[str, Any] = response.json()
            self.pr_reads += 1
            # The second read has an unchanged head, timestamp and file count.
            if self.pr_reads % 2 == 0:
                if self.fault == "base_retargeted":
                    current["base"]["ref"] = "release"
                else:
                    current["base"]["sha"] = INTERMEDIATE
            return httpx.Response(200, json=current)
        if self.fault == "omitted_intervening_files_link" and path.startswith("/commits/"):
            page = request.url.params.get("page")
            if path == f"/commits/{INTERMEDIATE}":
                self.requests.append(f"{path}?page={page}")
                assert request.method == "GET"
                files = (
                    [
                        {"filename": f"unrelated/{index}", "status": "modified"}
                        for index in range(100)
                    ]
                    if page == "1"
                    else [{"filename": "tests/test_main.py", "status": "modified"}]
                )
                return httpx.Response(200, json={"files": files})
            if path == f"/commits/{MAIN_SHA}":
                return httpx.Response(200, json={"files": []})
        main_annot = [annotation("tests/test_main.py", "test_main")]
        main_passed = [passed_test("tests/test_main.py", "test_main")]
        if self.fault == "mixed_main_failures":
            main_annot.append(annotation("changed.py", "test_changed"))
            main_passed.append(passed_test("changed.py", "test_changed"))
        if self.fault == "green_without_pass_evidence":
            main_passed[0]["message"] = "Tests passed"
        if self.fault == "green_with_different_test":
            main_passed[0]["title"] = "test_other"
        pr_count = int(self.fault == "annotations_unavailable")
        pr_checks = [
            check(101, "same-head", "failure", FIRST, pr_count),
            check(103, "outside-files", "failure", FIRST, 1),
        ]
        if self.step:
            pr_checks[0] = check(102, "same-head", "success", FIRST, 0)
        if self.late_annotation:
            pr_checks.append(check(101, "same-head", "failure", FIRST, 1))
        failed_main_check = check(104, "test", "failure", FAILED_MAIN, len(main_annot))
        if self.fault == "absent_run_url":
            del failed_main_check["html_url"]
            del pr_checks[1]["html_url"]
        main_changed = self.fault == "renamed_main_test"
        changed = (
            {
                "filename": "renamed.py",
                "status": "renamed",
                "previous_filename": "tests/test_main.py",
            }
            if main_changed
            else {"filename": "changed.py", "status": "modified"}
        )
        main_run = {
            **status(201, "ci", "success"),
            "workflow_id": 40,
            "head_sha": MAIN_SHA,
            "head_branch": "trunk",
            "repository": {"full_name": REPO},
            "head_repository": {"full_name": REPO},
            "event": "push",
            "run_attempt": 1,
            "created_at": LATER,
        }
        older_run = {
            **main_run,
            **status(200, "ci", "failure"),
            "workflow_id": 41 if self.fault == "other_workflow" else 40,
            "head_sha": FAILED_MAIN,
            "created_at": NOW,
        }
        if self.fault == "foreign_main_success":
            main_run["head_repository"] = {"full_name": "contributor/fork"}
            main_run["event"] = "pull_request"
        responses = {
            f"/commits/{FIRST}/check-runs": {
                "check_runs": pr_checks,
                "total_count": len(pr_checks),
            },
            f"/commits/{FAILED_MAIN}/check-runs": {
                "check_runs": [failed_main_check],
                "total_count": 1,
            },
            f"/commits/{MAIN_SHA}/check-runs": {
                "check_runs": [check(105, "test", "success", MAIN_SHA, len(main_passed))],
                "total_count": 1,
            },
            "/actions/runs": {"workflow_runs": [main_run, older_run]},
            "/actions/runs/200/attempts/1/jobs": {"jobs": [self.job(2000, 104, "failure")]},
            "/actions/runs/201/attempts/1/jobs": {"jobs": [self.job(2001, 105, "success")]},
            "/check-runs/103/annotations": [annotation("tests/test_outside.py", "test_outside")],
            "/check-runs/104/annotations": main_annot,
            "/check-runs/105/annotations": main_passed,
            COMPARE: {
                "status": "ahead",
                "base_commit": {"sha": FAILED_MAIN},
                "merge_base_commit": {"sha": FAILED_MAIN},
                "total_commits": 2
                if self.fault
                in {
                    "incomplete_comparison",
                    "test_changed_and_reverted",
                    "omitted_intervening_files_link",
                }
                else 1,
                "commits": [
                    *(
                        [{"sha": INTERMEDIATE}]
                        if self.fault
                        in {"test_changed_and_reverted", "omitted_intervening_files_link"}
                        else []
                    ),
                    {"sha": MAIN_SHA},
                ],
                "files": [changed],
            },
            f"/commits/{INTERMEDIATE}": {
                "files": [{"filename": "tests/test_main.py", "status": "modified"}]
            },
            f"/commits/{MAIN_SHA}": {
                "files": [{"filename": "tests/test_main.py", "status": "modified"}]
            },
        }
        if path == "/check-runs/101/annotations":
            self.requests.append(path)
            assert request.method == "GET"
            if self.late_annotation:
                return httpx.Response(200, json=[annotation("late.py", "test_late")])
            return httpx.Response(503)
        if path in responses:
            self.requests.append(path)
            assert request.method == "GET"
            return httpx.Response(200, json=responses[path])
        return super().handle(request)

    @staticmethod
    def job(identifier: int, check_id: int, conclusion: str) -> dict[str, Any]:
        return {
            **status(identifier, "test", conclusion),
            "check_run_url": f"{API}/repos/{REPO}/check-runs/{check_id}",
        }


@pytest.mark.parametrize(
    ("fault", "expected", "main_proven"),
    [
        ("", {101, 103, 104}, True),
        ("absent_run_url", {101, 103, 104}, True),
        ("late_annotations", {101, 103, 104}, True),
        ("base_retargeted", {101, 104}, True),
        ("base_advanced", {101, 104}, True),
        ("green_without_pass_evidence", {101, 103}, False),
        ("green_with_different_test", {101, 103}, False),
        ("annotations_unavailable", {103, 104}, True),
        ("incomplete_comparison", {101, 103}, False),
        ("test_changed_and_reverted", {101, 103}, False),
        ("omitted_intervening_files_link", {101, 103}, False),
        ("renamed_main_test", {101, 103}, False),
        ("other_workflow", {101, 103}, False),
        ("foreign_main_success", {101, 103}, False),
        ("mixed_main_failures", {101, 103, 104}, False),
    ],
)
async def test_flake_patterns_are_durable_and_read_only(
    tmp_path: Path, fault: str, expected: set[int], main_proven: bool
) -> None:
    fixture = FlakeFixture(fault)
    clock = FixedClock()
    credential = LocalGitHubCredential("TOKEN", {"TOKEN": "fixture"}, None)
    api = GitHubReadClient(API, REPO)
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            for restart in (False, True):
                if restart:
                    await store.close()
                    store = SqliteStore("demo", tmp_path)
                ledger = Ledger(
                    store, build_catalog(github_repository=REPO), clock, github_repository=REPO
                )
                ledger.start()
                puller = GitHubPoller(
                    ledger, clock, REPO, credential, PullRequestFetcher(API, REPO), 1
                )
                details = GitHubDetailPoller(ledger, clock, api, credential)
                try:
                    if not restart:
                        await puller.poll(client)
                        await details.poll(client)
                        fixture.step = 1
                    await details.poll(client)
                    _, items = await ledger.read_next("clerk", 0, 100)
                    flakes = [item for item in items if item.kind == "flake_candidate"]
                    assert len(flakes) == (0 if restart else len(expected))
                    for item in flakes:
                        await ledger.execute("clerk", "ack", item.id, {"item": item.id})
                    if fault == "late_annotations" and not restart:
                        fixture.late_annotation = True
                        await details.poll(client)
                        _, items = await ledger.read_next("clerk", 0, 100)
                        assert all(item.kind != "flake_candidate" for item in items)
                finally:
                    await ledger.stop()
        events = await store.events_after(0)
        candidates = {
            event.fields["check_id"]: event for event in events if event.kind == FLAKE_EVENT
        }
        assert candidates.keys() == expected
        assert sum(event.kind == FLAKE_EVENT for event in events) == len(expected)
        if 101 in candidates:
            fallback = candidates[101]
            assert fallback.fields["pattern"] == "same_head_pass"
            assert fallback.fields["run_url"] == f"https://github.com/{REPO}/runs/101"
            assert fallback.fields["check_name"] == "same-head"
            assert "test_id" not in fallback.fields
        if 103 in candidates:
            assert candidates[103].fields["test_id"] == "test_outside"
            assert candidates[103].fields["pattern"] == "pr_unchanged_test"
            assert len(candidates[103].body) == 500
        if 104 in candidates:
            assert candidates[104].fields["test_id"] == "test_main"
            assert candidates[104].fields["pattern"] == "main_unchanged_test_pass"
            assert candidates[104].fields["success_head_sha"] == MAIN_SHA
        if fault == "absent_run_url":
            assert "run_url" not in candidates[103].fields
            assert "run_url" not in candidates[104].fields
        if fault == "late_annotations":
            assert "/check-runs/101/annotations" in fixture.requests
        if fault == "omitted_intervening_files_link":
            assert f"/commits/{INTERMEDIATE}?page=2" in fixture.requests
        assert sum(event.kind == MAIN_FLAKE_EVENT for event in events) == int(main_proven)
        if fault != "incomplete_comparison":
            assert fixture.requests.count(COMPARE) == int(
                fault not in {"other_workflow", "foreign_main_success"}
            )
    finally:
        await store.close()


async def test_pr_flake_writer_refuses_an_updated_pull_with_the_same_version() -> None:
    observed = PullRequest(REPO, 4, "worker", FIRST, REPO, "open", NOW, NOW, True, 1)
    uow = create_autospec(UnitOfWork, instance=True)
    uow.pull_request.return_value = replace(observed, updated_at=LATER)
    checks = GitHubResource(
        (
            GitHubRecord(
                "103",
                "check_run",
                {**status(103, "test", "failure"), "head_sha": FIRST},
                details={
                    "annotations": [{"path": "test.py", "test_id": "test", "excerpt": "failed"}],
                },
            ),
        )
    )
    files = GitHubResource(
        (),
        attributes={
            "head_sha": FIRST,
            "base_ref": "trunk",
            "base_sha": MAIN_SHA,
            "pull_version": observed.version,
            "pull_updated_at": observed.updated_at,
        },
    )
    await record_pull_flakes(uow, REPO, observed, checks, files)
    uow.append.assert_not_awaited()


SAME_HEAD_RECOVERIES: list[tuple[bool, tuple[dict[str, Any], ...], int | None]] = [
    (True, ({},), None),
    (True, ({"passed_tests": []},), None),
    (True, ({"passed_tests": [{"path": "test.py", "test_id": "different"}]},), None),
    (False, ({"junit_state": "deferred"}, {}), None),
    (False, ({"junit_miss_remaining": 2, "passed_tests": []},), 102),
    (
        False,
        (
            {"junit_evidence": True, "passed_tests": [{"path": "test.py", "test_id": "other"}]},
            {"passed_tests": [{"path": "test.py", "test_id": "unrelated"}]},
        ),
        None,
    ),
    (
        False,
        (
            {"junit_evidence": True, "passed_tests": [{"path": "test.py", "test_id": "other"}]},
            {"passed_tests": [{"path": "test.py", "test_id": "test"}]},
        ),
        103,
    ),
    (
        True,
        (
            {"junit_evidence": True, "passed_tests": []},
            {"passed_tests": [{"path": "test.py", "test_id": "test"}]},
        ),
        103,
    ),
    (False, ({},), 102),
]


@pytest.mark.parametrize(("junit_failure", "passes", "expected_id"), SAME_HEAD_RECOVERIES)
async def test_same_head_structured_recovery_evaluates_later_test_results(
    junit_failure: bool, passes: tuple[dict[str, Any], ...], expected_id: int | None
) -> None:
    failure = GitHubRecord(
        "101",
        "check_run",
        {**status(101, "test", "failure"), "head_sha": FIRST},
        details={
            "app_id": 8,
            "suite_id": 7,
            "completed_at": 1,
            "junit_evidence": junit_failure,
            "annotations": [{"path": "test.py", "test_id": "test", "excerpt": "failed"}],
        },
    )
    successes = tuple(
        GitHubRecord(
            str(identifier),
            "check_run",
            {**status(identifier, "test", "success"), "head_sha": FIRST},
            details={"app_id": 8, "suite_id": 7, "completed_at": 2, **details},
        )
        for identifier, details in enumerate(passes, 102)
    )
    checks = GitHubResource(
        successes,
        attributes={
            "prior_failures": {failure.key: {"fields": failure.fields, "details": failure.details}}
        },
    )
    uow = create_autospec(UnitOfWork, instance=True)
    uow.find_event.return_value = None
    uow.append.return_value = SimpleNamespace(created=False)
    await record_same_head(uow, REPO, checks)
    if expected_id is None:
        uow.append.assert_not_awaited()
    else:
        uow.append.assert_awaited_once()
        assert uow.append.await_args.args[0].fields["success_check_id"] == expected_id

    runs = tuple(
        GitHubRecord(
            check.key,
            "workflow_run",
            {
                "head_sha": FIRST,
                "jobs": [{**check.fields, "check_run_id": check.fields["id"]}],
            },
            details={"workflow_id": 40},
        )
        for check in (*reversed(successes), failure)
    )
    uow.reset_mock()
    pairs = main_pairs(runs, {FIRST: checks})
    assert len(pairs) == len(successes)
    for pair in pairs:
        await record_main_flakes(uow, REPO, pair, None)
    if expected_id is None:
        uow.append.assert_not_awaited()
    else:
        candidate, proof = [call.args[0] for call in uow.append.await_args_list]
        assert candidate.kind == FLAKE_EVENT
        assert candidate.fields["success_check_id"] == expected_id
        assert proof.kind == MAIN_FLAKE_EVENT


@pytest.mark.parametrize("legacy", [False, True])
async def test_known_empty_failure_waits_for_successful_junit_resolution(legacy: bool) -> None:
    failure = GitHubRecord(
        "101",
        "check_run",
        {**status(101, "test", "failure"), "head_sha": FIRST},
        details={"run_url": "https://example.test/run/101", "annotations": []},
    )
    # An observed transition on the same check ID is enough for check-only fallback.
    success = GitHubRecord(
        failure.key,
        "check_run",
        {**status(101, "test", "success"), "head_sha": FIRST},
        details={"junit_state": "deferred"},
    )
    checks = GitHubResource(
        (success,),
        attributes={
            "prior_failures": {failure.key: {"fields": failure.fields, "details": failure.details}}
        },
    )
    uow = create_autospec(UnitOfWork, instance=True)
    uow.append.return_value = SimpleNamespace(created=False)
    await record_same_head(uow, REPO, checks)
    await record_main_flakes(uow, REPO, MainPair(failure, success), None)
    uow.append.assert_not_awaited()

    resolved = replace(success, details={} if legacy else {"junit_miss_remaining": 4})
    await record_same_head(uow, REPO, replace(checks, records=(resolved,)))
    uow.append.assert_awaited_once()
    candidate = uow.append.await_args.args[0]
    assert candidate.kind == FLAKE_EVENT
    assert "test_id" not in candidate.fields

    uow.reset_mock()
    await record_main_flakes(uow, REPO, MainPair(failure, resolved), None)
    candidate, proof = [call.args[0] for call in uow.append.await_args_list]
    assert candidate.kind == FLAKE_EVENT
    assert "test_id" not in candidate.fields
    assert proof.kind == MAIN_FLAKE_EVENT
