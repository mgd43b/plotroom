"""Rule 2 over complete, head-pinned review-thread evidence."""

from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import pytest

from fakes import commit_status_evidence
from plotroom.adapters.github.pr_details import fetch_threads
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.core import merge_gate
from plotroom.core.events import format_time
from plotroom.core.github import PullRequest
from plotroom.core.github_projection import GitHubRecord, GitHubResource
from plotroom.core.merge_gate import GatePolicy, GateSnapshot, evaluate_gate
from test_github_details import API, FIRST, NOW, REPO, SECOND, Fixture

CHECKED_AT = datetime.fromisoformat(NOW)
THREADS_OBSERVED = format_time(CHECKED_AT - timedelta(seconds=5))
PULL = PullRequest(REPO, 4, "worker", FIRST, REPO, "open", NOW, NOW, True, 1)
CHECK = GitHubRecord(
    "1",
    "check_run",
    {"id": 1, "name": "test", "head_sha": FIRST, "status": "completed", "conclusion": "success"},
)
EMPTY_STATUS_EVIDENCE = commit_status_evidence(REPO, FIRST, NOW)
EMPTY_THREADS = GitHubResource(
    (), attributes={"head_sha": FIRST, "pull_version": PULL.version}, as_of=THREADS_OBSERVED
)


async def test_gate_evaluates_every_review_thread_and_its_refreshed_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(merge_gate, "GATE_RULES", merge_gate.GATE_RULES[:2])
    fixture = Fixture()
    fixture.first_thread_page_size = 100
    fixture.step = 1
    api = GitHubReadClient(API, REPO)
    snapshot = GateSnapshot(
        PULL, GitHubResource((CHECK,), as_of=NOW), commit_statuses=EMPTY_STATUS_EVIDENCE
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        unresolved = await fetch_threads(api, client, "fixture", PULL)
        assert len(unresolved.records) == 101
        assert fixture.requests == ["/graphql", "/graphql"]
        snapshot = replace(snapshot, threads=replace(unresolved, as_of=THREADS_OBSERVED))
        refusal = evaluate_gate(snapshot, GatePolicy(1), CHECKED_AT)
        assert (refusal.verdict, refusal.exit_class, refusal.as_of) == (
            "REFUSE",
            2,
            THREADS_OBSERVED,
        )
        [reason] = refusal.reasons
        assert (reason.code, reason.detail) == ("review_threads_unresolved", "1 unresolved threads")

        fixture.resolve_last_thread = True
        resolved = await fetch_threads(api, client, "fixture", PULL)
    assert len(resolved.records) == 101
    refreshed = replace(snapshot, threads=replace(resolved, as_of=NOW))
    assert evaluate_gate(refreshed, GatePolicy(1), CHECKED_AT).verdict == "MERGE"


async def test_gate_requires_new_thread_observation_after_same_sha_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(merge_gate, "GATE_RULES", merge_gate.GATE_RULES[:2])
    fixture = Fixture()
    fixture.step = 1
    fixture.resolve_last_thread = True
    api = GitHubReadClient(API, REPO)
    checks = GitHubResource((CHECK,), as_of=NOW)
    reopened = replace(PULL, version=PULL.version + 2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        original = await fetch_threads(api, client, "fixture", PULL)
        original = replace(original, as_of=THREADS_OBSERVED)
        snapshot = GateSnapshot(
            PULL, checks, threads=original, commit_statuses=EMPTY_STATUS_EVIDENCE
        )
        assert evaluate_gate(snapshot, GatePolicy(1), CHECKED_AT).verdict == "MERGE"

        snapshot = replace(snapshot, pull=reopened)
        [reason] = evaluate_gate(snapshot, GatePolicy(1), CHECKED_AT).reasons
        assert reason.code == "review_threads_incomplete"

        refreshed = await fetch_threads(api, client, "fixture", reopened)
    snapshot = replace(snapshot, threads=replace(refreshed, as_of=NOW))
    assert evaluate_gate(snapshot, GatePolicy(1), CHECKED_AT).verdict == "MERGE"


@pytest.mark.parametrize(
    "threads",
    [
        None,
        replace(EMPTY_THREADS, complete=False),
        replace(EMPTY_THREADS, attributes={}),
        replace(EMPTY_THREADS, attributes={"head_sha": FIRST}),
        replace(EMPTY_THREADS, attributes={**EMPTY_THREADS.attributes, "head_sha": SECOND}),
        replace(EMPTY_THREADS, as_of="2026-11-22T11:00:00Z"),
        replace(EMPTY_THREADS, as_of="2026-11-22T12:00:01Z"),
    ],
)
def test_missing_incomplete_wrong_head_or_stale_threads_refuse(
    threads: GitHubResource | None,
) -> None:
    snapshot = GateSnapshot(
        PULL,
        GitHubResource((CHECK,), as_of=NOW),
        commit_statuses=EMPTY_STATUS_EVIDENCE,
        threads=threads,
    )
    decision = evaluate_gate(snapshot, GatePolicy(1), CHECKED_AT)
    assert (decision.verdict, decision.exit_class) == ("REFUSE", 2)
    [reason] = decision.reasons
    assert (reason.code, reason.detail) == (
        "review_threads_incomplete",
        "could not read the review threads",
    )


def test_rule_1_failure_precedes_missing_thread_evidence() -> None:
    failure = replace(CHECK, fields={**CHECK.fields, "conclusion": "failure"})
    snapshot = GateSnapshot(
        PULL, GitHubResource((failure,), as_of=NOW), commit_statuses=EMPTY_STATUS_EVIDENCE
    )
    [reason] = evaluate_gate(snapshot, GatePolicy(1), CHECKED_AT).reasons
    assert reason.code == "checks_failing"
