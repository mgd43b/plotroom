"""Fail-closed provider contracts and shared merge readiness for opt-in Kodus review."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from plotroom.adapters.github import GitHubUnavailable, details
from plotroom.adapters.github.kodus_review import fetch_kodus_review
from plotroom.adapters.github.read import CADENCE_THREADS_QUERY, GitHubReadClient, Row
from plotroom.app.config import ConfigError, load_config
from plotroom.core.events import format_time
from plotroom.core.github import PullRequest
from plotroom.core.github_projection import KODUS_REVIEW, PULL_TEXT, GitHubResource
from plotroom.core.kodus_review import KodusReviewPolicy, OpeningHeadAttestation
from test_merge_controller_e2e import (
    API,
    LATER,
    MAIN,
    NOW,
    REPO,
    Pull,
    Repository,
    assert_live_verdict,
    call,
    events,
    next_items,
    running_daemon,
)
from test_merge_controller_e2e import (
    repository as repository,
)

APP = KodusReviewPolicy(54821, "fixture-kodus", "Kody Code Review")
REVIEWED = "Review finished successfully. Suggestions (if any) were posted as PR/file comments."
CADENCE_SKIP = "Manual mode requires @kody start-review."
OPENING = "a" * 40
CREATED_AT = format_time(datetime.fromisoformat(NOW))


def check(pull: Pull, sha: str, *, skipped: bool = False) -> Row:
    detail = pull.row()
    return {
        "id": 9876 if skipped else 1234,
        "name": APP.check_name,
        "head_sha": sha,
        "status": "completed",
        "conclusion": "skipped" if skipped else "success",
        "started_at": NOW,
        "completed_at": LATER,
        "output": {"summary": CADENCE_SKIP if skipped else REVIEWED},
        "app": {"id": APP.app_id, "slug": APP.app_slug},
        "pull_requests": [
            {
                "id": detail["id"],
                "number": pull.number,
                "head": {**detail["head"], "sha": sha},
                "base": detail["base"],
            }
        ],
    }


def form(pull: Pull, sha: str) -> Row:
    return {
        "id": 612,
        "pull_request_url": f"{API}/repos/{REPO}/pulls/{pull.number}",
        "commit_id": sha,
        "state": "COMMENTED",
        "submitted_at": LATER,
        "user": {"login": f"{APP.app_slug}[bot]", "type": "Bot"},
    }


def reply(identifier: str, *, root: bool = False) -> Row:
    return {
        "id": identifier,
        "replyTo": None if root else {"id": "review"},
        "author": {
            "login": f"{APP.app_slug}[bot]" if root else "developer",
            "__typename": "Bot" if root else "User",
        },
        "authorAssociation": "NONE",
    }


class Provider:
    def __init__(self, *, cadence: bool = False) -> None:
        self.pull = Pull(23)
        self.pr = PullRequest(REPO, 23, "pr-23", self.pull.head, REPO, "open", NOW, NOW, True, 2)
        self.detail: Row = {**self.pull.row(), "user": {"login": "developer"}, "node_id": "PR_node"}
        self.association = OpeningHeadAttestation(REPO, 23, self.detail["id"], CREATED_AT, OPENING)
        self.policy = replace(APP, opening_heads=(self.association,)) if cadence else APP
        self.current = check(self.pull, self.pr.head_sha, skipped=cadence)
        if cadence:
            self.current["started_at"] = LATER
        self.opening = check(self.pull, OPENING)
        self.check_count = 1
        self.thread: Row = {
            "id": "thread",
            "isResolved": True,
            "comments": {
                "totalCount": 2,
                "nodes": [reply("review", root=True), reply("answer")],
                "pageInfo": {"hasNextPage": False},
            },
        }
        self.thread_count = 1
        self.reviews: list[Row] = []
        self.paths: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path == "/graphql":
            assert request.method == "POST"
            assert CADENCE_THREADS_QUERY.strip().splitlines()[0] in request.content.decode()
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "nameWithOwner": REPO,
                            "pullRequest": {
                                "id": "PR_node",
                                "number": self.pr.number,
                                "headRefOid": self.pr.head_sha,
                                "author": {"login": "developer"},
                                "reviewThreads": {
                                    "nodes": [self.thread],
                                    "totalCount": self.thread_count,
                                    "pageInfo": {"hasNextPage": False},
                                },
                            },
                        }
                    }
                },
            )
        assert request.method == "GET"
        path = path.removeprefix(f"/repos/{REPO}")
        if path == f"/pulls/{self.pr.number}":
            return httpx.Response(200, json=self.detail)
        if path == f"/pulls/{self.pr.number}/reviews":
            return httpx.Response(200, json=self.reviews)
        assert path.startswith("/commits/")
        assert path.endswith("/check-runs")
        assert request.url.params["filter"] == "latest"
        assert request.url.params["check_name"] == APP.check_name
        sha = path.split("/")[2]
        assert sha in {OPENING, self.pull.head}
        return httpx.Response(
            200,
            json={
                "total_count": self.check_count,
                "check_runs": [self.opening if sha == OPENING else self.current],
            },
        )

    async def read(self) -> Row:
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.handle)) as client:
            resource = await fetch_kodus_review(
                GitHubReadClient(API, REPO), client, "fixture", self.pr, self.policy
            )
        assert resource.complete
        return resource.attributes


async def test_direct_review_without_an_opening_attestation_or_findings() -> None:
    provider = Provider()
    result = await provider.read()
    assert (result["mode"], result["head_sha"], result["pull_id"], result["app_id"]) == (
        "reviewed",
        provider.pr.head_sha,
        provider.detail["id"],
        APP.app_id,
    )
    assert all(not path.endswith("/reviews") for path in provider.paths)


@pytest.mark.parametrize(
    "fault", ["repository", "pull", "head", "status", "summary", "app", "slug", "count"]
)
async def test_untrusted_or_incomplete_direct_check_refuses(fault: str) -> None:
    provider = Provider()
    if fault == "repository":
        provider.detail["base"]["repo"]["full_name"] = "other/place"
    elif fault == "pull":
        provider.current["pull_requests"][0]["id"] += 1
    elif fault == "head":
        provider.current["head_sha"] = OPENING
    elif fault == "status":
        provider.current["status"] = "in_progress"
    elif fault == "summary":
        provider.current["output"]["summary"] = "Review started"
    elif fault == "app":
        provider.current["app"]["id"] += 1
    elif fault == "slug":
        provider.current["app"]["slug"] = "different"
    else:
        provider.check_count += 1
    with pytest.raises(GitHubUnavailable):
        await provider.read()


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "mixed-case",
        "wrong-pr",
        "wrong-origin",
        "head",
        "bot",
        "window",
        "conflict",
        "duplicate",
        "empty",
    ],
)
async def test_exact_pr_review_form_corroborates_only_an_empty_success(
    fault: str | None,
) -> None:
    provider = Provider()
    provider.current["pull_requests"] = []
    row = form(provider.pull, provider.pr.head_sha)
    provider.reviews = [row]
    if fault == "mixed-case":
        row["pull_request_url"] = f"{API}/repos/Fixture/Epic4/pulls/{provider.pr.number}"
    elif fault == "wrong-pr":
        row["pull_request_url"] = f"{API}/repos/{REPO}/pulls/{provider.pr.number + 1}"
    elif fault == "wrong-origin":
        row["pull_request_url"] = f"https://another.example/repos/{REPO}/pulls/{provider.pr.number}"
    elif fault == "head":
        row["commit_id"] = OPENING
    elif fault == "bot":
        row["user"]["type"] = "User"
    elif fault == "window":
        row["submitted_at"] = "2026-11-26T20:01:40Z"
    elif fault == "conflict":
        provider.reviews.append({**row, "id": 613, "commit_id": OPENING})
    elif fault == "duplicate":
        provider.reviews.append(row)
    elif fault == "empty":
        provider.reviews.clear()
    if fault in (None, "mixed-case"):
        assert (await provider.read())["mode"] == "reviewed"
    else:
        with pytest.raises(GitHubUnavailable):
            await provider.read()


@pytest.mark.parametrize(
    "fault",
    [
        "no-attestation",
        "pull-id",
        "created-at",
        "same-sha",
        "no-opening-review",
        "unlinked-skip",
        "generic-skip",
        "unresolved",
        "unanswered",
        "partial-threads",
        "partial-comments",
        "reviewer-reply",
        "bot-reply",
    ],
)
async def test_cadence_skip_requires_trusted_opening_and_complete_answered_threads(
    fault: str,
) -> None:
    provider = Provider(cadence=True)
    if fault == "no-attestation":
        provider.policy = APP
    elif fault == "pull-id":
        provider.policy = replace(APP, opening_heads=(replace(provider.association, pull_id=1),))
    elif fault == "created-at":
        provider.policy = replace(
            APP, opening_heads=(replace(provider.association, created_at=LATER),)
        )
    elif fault == "same-sha":
        provider.policy = replace(
            APP, opening_heads=(replace(provider.association, head_sha=provider.pr.head_sha),)
        )
    elif fault == "no-opening-review":
        provider.opening["status"] = "in_progress"
    elif fault == "unlinked-skip":
        provider.current["pull_requests"] = []
        provider.reviews = [form(provider.pull, provider.pr.head_sha)]
    elif fault == "generic-skip":
        provider.current["output"]["summary"] = "Review is disabled"
    elif fault == "unresolved":
        provider.thread["isResolved"] = False
    elif fault == "unanswered":
        provider.thread["comments"]["nodes"][1]["author"]["login"] = "visitor"
    elif fault == "partial-threads":
        provider.thread_count += 1
    elif fault == "partial-comments":
        provider.thread["comments"]["pageInfo"]["hasNextPage"] = True
    elif fault == "reviewer-reply":
        comments = provider.thread["comments"]["nodes"]
        comments[0]["author"]["login"] = comments[1]["author"]["login"]
    elif fault == "bot-reply":
        provider.thread["comments"]["nodes"][1]["author"]["__typename"] = "Bot"
    with pytest.raises(GitHubUnavailable):
        await provider.read()


async def test_attested_opening_review_and_answered_threads_allow_cadence_skip() -> None:
    provider = Provider(cadence=True)
    assert provider.opening["completed_at"] == provider.current["started_at"]
    evidence = await provider.read()
    assert (evidence["mode"], evidence["opening_sha"], evidence["opening_check_id"]) == (
        "cadence-skip",
        OPENING,
        provider.opening["id"],
    )


async def test_cadence_skip_started_before_opening_review_completed_is_refused() -> None:
    provider = Provider(cadence=True)
    provider.opening["completed_at"] = "2026-11-22T12:00:01.000001Z"
    with pytest.raises(GitHubUnavailable, match="skip started before opening review completed"):
        await provider.read()
    assert "/graphql" not in provider.paths


async def test_opening_review_with_conflicting_associated_head_is_refused() -> None:
    provider = Provider(cadence=True)
    provider.opening["pull_requests"][0]["head"]["sha"] = "f" * 40
    with pytest.raises(GitHubUnavailable, match="lacks a verified review or cadence skip"):
        await provider.read()
    assert "/graphql" not in provider.paths


async def test_kodus_poll_prioritizes_ordinary_evidence_and_rotates_budgeted_open_pulls(
    tmp_path: Path, repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, third = Pull(520), Pull(521), Pull(522)
    closed, merged = Pull(523, closed=True), Pull(524, merge_commit="9" * 40)
    targets = (first, second, third, closed, merged)
    repository.add(*targets)
    elapsed = 0.0
    timeouts: list[float] = []
    attempts: list[int] = []

    def monotonic() -> float:
        return elapsed

    def timeout(seconds: float) -> asyncio.Timeout:
        timeouts.append(seconds)
        return asyncio.timeout(seconds)

    async with running_daemon(tmp_path, kodus_review=APP, max_age=timedelta(seconds=8)) as (
        daemon,
        _,
        _,
        _,
    ):
        ledger = daemon.ledgers["epic4"]

        async def read(
            api: GitHubReadClient,
            client: httpx.AsyncClient,
            token: str,
            pull: PullRequest,
            policy: KodusReviewPolicy,
        ) -> GitHubResource:
            nonlocal elapsed
            for target in targets:
                assert await ledger.github_resource(REPO, PULL_TEXT, str(target.number)) is not None
            attempts.append(pull.number)
            took = min(1.25, timeouts[-1])
            elapsed += took
            if pull.number == first.number or took < 1.25:
                raise TimeoutError
            return GitHubResource(())

        monkeypatch.setattr(details, "monotonic", monotonic)
        monkeypatch.setattr(details, "asyncio", SimpleNamespace(timeout=timeout))
        monkeypatch.setattr(details, "fetch_kodus_review", read)

        for expected in (
            [first.number, second.number],
            [first.number, second.number, third.number, first.number],
            [first.number, second.number, third.number, first.number, second.number, third.number],
        ):
            await repository.refresh()
            assert attempts == expected
        assert timeouts == [2.0, 0.75] * 3
        first_proof = await ledger.github_resource(REPO, KODUS_REVIEW, str(first.number))
        assert first_proof is not None
        assert first_proof.generation == 2
        assert not first_proof.complete
        for pull in (closed, merged):
            assert await ledger.github_resource(REPO, KODUS_REVIEW, str(pull.number)) is None


async def test_kodus_poll_advisory_live_and_pinned_confirmation(
    tmp_path: Path, repository: Repository
) -> None:
    pull = Pull(511)
    repository.add(pull)
    current = check(pull, pull.head)
    repository.review_runs[pull.head] = [current]
    async with running_daemon(tmp_path, kodus_review=APP) as (daemon, _, clerk, _):
        await repository.refresh()
        [offer] = await next_items(clerk, "merge_ready")
        args = offer["context"]["merge_gate"]
        assert args["expected_main_sha"] == MAIN
        advisory = await call(clerk, "merge_gate", pr=pull.number)
        assert advisory["verdict"] == "MERGE"
        await assert_live_verdict(daemon, pull, advisory)
        assert (await call(clerk, "merge_gate", **args))["verdict"] == "MERGE"

        # Provider proof changes without a PR head, PR update time, or CI state change.
        current["output"]["summary"] = "Review started"
        pinned = await call(clerk, "merge_gate", **args)
        assert pinned["verdict"] == "REFUSE"
        assert pinned["reasons"][0]["code"] == "kodus_review_unverified"
        await repository.refresh()
        assert await next_items(clerk, "merge_ready") == []
        advisory = await call(clerk, "merge_gate", pr=pull.number)
        assert advisory["reasons"][0]["code"] == "kodus_review_unverified"
        await assert_live_verdict(daemon, pull, advisory)


@pytest.mark.parametrize("changed", ["summary", "association", "started_at"])
async def test_ci_green_does_not_reuse_a_changed_same_id_review(
    tmp_path: Path, repository: Repository, changed: str
) -> None:
    pull = Pull(512, checks=(None, "success"))
    repository.add(pull)
    current = check(pull, pull.head)
    repository.review_runs[pull.head] = [current]
    async with running_daemon(tmp_path, kodus_review=APP) as (daemon, _, clerk, _):
        await repository.refresh()
        advisory = await call(clerk, "merge_gate", pr=pull.number)
        assert advisory["reasons"][0]["code"] == "checks_pending"

        pull.checks = ("success", "success")
        if changed == "summary":
            current["output"]["summary"] = "Review started"
        elif changed == "association":
            current["pull_requests"][0]["base"]["repo"]["id"] += 1
        else:
            current["started_at"] = "2026-11-22T11:59:59Z"
        await repository.refresh()
        assert await next_items(clerk, "merge_ready") == []
        advisory = await call(clerk, "merge_gate", pr=pull.number)
        assert advisory["reasons"][0]["code"] == "kodus_review_unverified"
        assert all(
            event.fields["verdict"] != "MERGE"
            for event in await events(daemon, "core.merge_verdict")
            if event.fields["number"] == pull.number
        )


async def test_cadence_provider_matches_advisory_live_and_pinned_confirmation(
    tmp_path: Path, repository: Repository
) -> None:
    pull = Pull(513, head_version=1)
    repository.add(pull)
    current = check(pull, pull.head, skipped=True)
    current["started_at"] = LATER
    repository.review_runs[pull.head] = [current]
    opening_check = check(pull, OPENING)
    opening_check["pull_requests"][0]["head"]["sha"] = pull.head
    repository.review_runs[OPENING] = [opening_check]
    repository.cadence_threads[pull.number] = [Provider(cadence=True).thread]
    opening = OpeningHeadAttestation(REPO, pull.number, pull.row()["id"], CREATED_AT, OPENING)
    policy = replace(APP, opening_heads=(opening,))
    async with running_daemon(tmp_path, kodus_review=policy) as (daemon, _, clerk, _):
        await repository.refresh()
        [offer] = await next_items(clerk, "merge_ready")
        pins = offer["context"]["merge_gate"]
        assert pins["expected_head_sha"] == pull.head
        assert pins["expected_main_sha"] == MAIN
        advisory = await call(clerk, "merge_gate", pr=pull.number)
        assert advisory["verdict"] == "MERGE"
        await assert_live_verdict(daemon, pull, advisory)
        assert (await call(clerk, "merge_gate", **pins))["verdict"] == "MERGE"


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "app_id = true",
        'app_slug = "Bad App"',
        f'repository = "{REPO}-other"',
        'head_sha = "abc"',
        'created_at = "2026-11-22T12:00:00"',
    ],
)
def test_project_local_configuration(tmp_path: Path, invalid: str | None) -> None:
    config_file = tmp_path / "config.toml"
    config = f"""
[projects.off.github]
repository = "{REPO}"
[projects.on.github]
repository = "{REPO}"
[projects.on.github.kodus_review]
app_id = {APP.app_id}
app_slug = "{APP.app_slug}"
check_name = "{APP.check_name}"
[[projects.on.github.kodus_review.opening_heads]]
repository = "{REPO}"
pull_number = 23
pull_id = 1000023
created_at = "{NOW}"
head_sha = "{OPENING}"
"""
    if invalid is not None:
        key = invalid.split(" = ")[0]
        lines = config.splitlines()
        index = max(index for index, line in enumerate(lines) if line.startswith(key + " = "))
        lines[index] = invalid
        config = "\n".join(lines)
    config_file.write_text(config, encoding="utf-8")
    if invalid is not None:
        with pytest.raises(ConfigError, match="kodus_review"):
            load_config(config_file, {})
        return
    loaded = load_config(config_file, {})
    assert loaded.github["off"].merge_gate_policy.kodus_review is None
    policy = loaded.github["on"].merge_gate_policy.kodus_review
    assert policy == replace(
        APP, opening_heads=(OpeningHeadAttestation(REPO, 23, 1_000_023, CREATED_AT, OPENING),)
    )
