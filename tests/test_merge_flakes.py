"""Exercise flake evidence through the read poller and the durable project writer."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from fakes import FixedClock
from plotroom.adapters.github import GitHubPoller, LocalGitHubCredential, PullRequestFetcher
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.catalog import build_catalog
from plotroom.core.ledger import Ledger
from plotroom.core.merge_flakes import FLAKE_EVENT, MAIN_FLAKE_EVENT
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

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(f"/repos/{REPO}")
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
                if self.fault in {"incomplete_comparison", "test_changed_and_reverted"}
                else 1,
                "commits": [
                    *([{"sha": INTERMEDIATE}] if self.fault == "test_changed_and_reverted" else []),
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
        ("green_without_pass_evidence", {101, 103}, False),
        ("green_with_different_test", {101, 103}, False),
        ("annotations_unavailable", {103, 104}, True),
        ("incomplete_comparison", {101, 103}, False),
        ("test_changed_and_reverted", {101, 103}, False),
        ("renamed_main_test", {101, 103}, False),
        ("other_workflow", {101, 103}, False),
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
        assert sum(event.kind == MAIN_FLAKE_EVENT for event in events) == int(main_proven)
        if fault != "incomplete_comparison":
            assert fixture.requests.count(COMPARE) == int(fault != "other_workflow")
    finally:
        await store.close()
