"""Read-only recovery of late merge identity through the actual detail poller."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from fakes import FixedClock
from plotroom.adapters.github import LocalGitHubCredential
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.catalog import build_catalog
from plotroom.core.github import DETAILS, PULL_REQUESTS, ObservedPull, apply_pull_poll
from plotroom.core.ledger import Ledger
from plotroom.core.merge_controller import MERGE_OBSERVATION_PENDING, MERGE_OBSERVED, MERGE_READY
from plotroom.core.unit_of_work import UnitOfWork

REPO = "fixture/repo"
API = "https://api.github.com"
HEAD = "a" * 40
MERGE_SHA = "b" * 40
OTHER_SHA = "c" * 40
OLD = "2026-11-27T10:00:00.000000Z"
NEWER = "2026-11-27T10:20:00.000000Z"
NOW = "2026-11-27T11:00:00.000000Z"
SECRET_PAYLOAD_MARKER = "fixture-provider-payload-should-not-appear-in-logs"


def opened(number: int) -> ObservedPull:
    return ObservedPull(number, f"fixture/{number}", HEAD, REPO, "open", OLD, "")


def merged(number: int, sha: str | None = None) -> ObservedPull:
    return replace(opened(number), state="merged", merge_commit_sha=sha)


@asynccontextmanager
async def running(directory: Path, clock: FixedClock) -> AsyncGenerator[tuple[Ledger, SqliteStore]]:
    store = SqliteStore("fixture", directory)
    await store.migrate()
    ledger = Ledger(
        store,
        build_catalog(frozenset(), github_repository=REPO),
        clock,
        github_repository=REPO,
        live_merge_controller=True,
    )
    ledger.start()
    try:
        await ledger.set_github_error(None)
        yield ledger, store
    finally:
        await ledger.stop()
        await store.close()


async def poll_pulls(ledger: Ledger, *pulls: ObservedPull) -> None:
    cursor = await ledger.fetch_cursor(REPO, PULL_REQUESTS)
    assert await ledger.run("system", lambda uow: apply_pull_poll(uow, REPO, cursor, pulls, NOW))


async def seed(ledger: Ledger, *numbers: int) -> None:
    await poll_pulls(ledger, *(opened(number) for number in numbers))
    await poll_pulls(ledger, *(merged(number) for number in numbers))
    await poll_pulls(ledger, replace(opened(99), updated_at=NEWER))


def graph_record(number: int, oid: str | None = MERGE_SHA) -> dict[str, Any]:
    return {
        "number": number,
        "repository": {"nameWithOwner": REPO},
        "merged": True,
        "state": "MERGED",
        "mergedAt": OLD,
        "headRefOid": HEAD,
        "mergeCommit": None if oid is None else {"oid": oid},
    }


def graph(record: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "repository": {
                    "nameWithOwner": REPO,
                    "pullRequest": record,
                }
            }
        },
    )


def rest(number: int, oid: str | None) -> dict[str, Any]:
    return {
        "number": number,
        "state": "closed",
        "merged": True,
        "merged_at": OLD,
        "updated_at": OLD,
        "head": {"sha": HEAD, "ref": f"fixture/{number}"},
        "base": {"repo": {"full_name": REPO}},
        "merge_commit_sha": oid,
    }


def requested_pr(request: httpx.Request) -> int:
    assert request.url.host == "api.github.com"
    if request.method == "POST":
        assert request.url.path == "/graphql"
        body = json.loads(request.content)
        assert body["query"].lstrip().startswith("query ")
        fields = body["variables"]
        assert (fields["owner"], fields["name"]) == ("fixture", "repo")
        return int(fields["number"])
    assert request.method == "GET"
    prefix = f"/repos/{REPO}/pulls/"
    assert request.url.path.startswith(prefix)
    return int(request.url.path.removeprefix(prefix))


def details(
    ledger: Ledger,
    clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
    phases: list[str] | None = None,
) -> GitHubDetailPoller:
    async def normal(_self: GitHubDetailPoller, _client: httpx.AsyncClient, _token: str) -> None:
        if phases is not None:
            phases.append("normal detail reads")

    monkeypatch.setattr(GitHubDetailPoller, "_poll_resources", normal)
    return GitHubDetailPoller(
        ledger,
        clock,
        GitHubReadClient(API, REPO),
        LocalGitHubCredential("TOKEN", {"TOKEN": "fixture"}, None),
    )


async def audit(ledger: Ledger, pr: int) -> list[tuple[str, dict[str, Any]]]:
    return [
        (event.kind, event.fields)
        for event in await ledger.events_after(0, limit=250)
        if event.fields.get("number") == pr
    ]


async def test_old_row_finalizes_once_and_preserves_list_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FixedClock(datetime.fromisoformat(NOW))
    async with running(tmp_path, clock) as (ledger, store):
        await poll_pulls(ledger, opened(1))
        await ledger.execute(
            "orchestrator",
            "trigger",
            "fixture-trigger",
            {
                "predicate": {"pr_merged": {"number": 1}},
                "action": {"notify_orchestrator": {"text": "fixture merge"}},
            },
        )
        before_merge = await ledger.fetch_cursor(REPO, PULL_REQUESTS)

        async def merge_with_offer(uow: UnitOfWork) -> None:
            await uow.raise_item(MERGE_READY, "1", "clerk", {"repository": REPO, "head_sha": HEAD})
            assert await apply_pull_poll(uow, REPO, before_merge, (merged(1),), NOW)

        await ledger.run("system", merge_with_offer)
        await poll_pulls(ledger, replace(opened(99), updated_at=NEWER))
        original_actions = await store.pending_actions(after=0, limit=3)
        assert len(original_actions) == 1
        expected_cursor = await ledger.fetch_cursor(REPO, PULL_REQUESTS)
        assert expected_cursor is not None
        assert expected_cursor.watermark == NEWER
        old = await ledger.pull_request(REPO, 1)
        assert old is not None
        assert old.updated_at == OLD
        before = await audit(ledger, 1)
        [pending] = [fields for kind, fields in before if kind == MERGE_OBSERVATION_PENDING]
        assert pending["classification"] == "gated"

    requests: list[tuple[str, int]] = []
    phases: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        phases.append("extra read")
        number = requested_pr(request)
        requests.append((request.method, number))
        return graph(graph_record(number))

    async with (
        running(tmp_path, clock) as (ledger, store),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client,
    ):
        poller = details(ledger, clock, monkeypatch, phases)
        await poller.poll(client)
        recovered = await ledger.pull_request(REPO, 1)
        assert recovered == replace(old, merge_commit_sha=MERGE_SHA)
        assert await ledger.fetch_cursor(REPO, PULL_REQUESTS) == expected_cursor
        after = await audit(ledger, 1)
        assert after == [*before, (MERGE_OBSERVED, {**pending, "merge_sha": MERGE_SHA})]
        await poller.poll(client)
        assert requests == [("POST", 1)]
        assert phases == ["normal detail reads", "extra read", "normal detail reads"]
        assert await audit(ledger, 1) == after
        assert await store.pending_actions(after=0, limit=3) == original_actions

    async with (
        running(tmp_path, clock) as (ledger, store),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client,
    ):
        await details(ledger, clock, monkeypatch).poll(client)
        assert requests == [("POST", 1)]
        assert await audit(ledger, 1) == after
        assert await ledger.fetch_cursor(REPO, PULL_REQUESTS) == expected_cursor
        assert await store.pending_actions(after=0, limit=3) == original_actions


@pytest.mark.parametrize(
    "oid",
    [MERGE_SHA, OTHER_SHA, "d" * 40],
    ids=["merge-commit", "squash-commit", "rebase-base-update"],
)
async def test_explicit_rest_fallback_uses_provider_oid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    oid: str,
) -> None:
    clock = FixedClock(datetime.fromisoformat(NOW))
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        pr = requested_pr(request)
        calls.append(request.method)
        if request.method == "POST":
            return graph(graph_record(pr, None))
        return httpx.Response(200, json=rest(pr, oid))

    async with (
        running(tmp_path, clock) as (ledger, _),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client,
    ):
        await seed(ledger, 1)
        await details(ledger, clock, monkeypatch).poll(client)
        row = await ledger.pull_request(REPO, 1)
        assert row is not None
        assert row.merge_commit_sha == oid
        assert calls == ["POST", "GET"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("number", 2),
        ("repository", {"nameWithOwner": "wrong/repo"}),
        ("headRefOid", OTHER_SHA),
        ("merged", False),
        ("state", "CLOSED"),
        ("mergedAt", None),
        ("mergeCommit", {"oid": "truncated-oid"}),
    ],
)
async def test_bad_graph_proof_leaves_pending_and_does_not_log_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    field: str,
    value: object,
) -> None:
    clock = FixedClock(datetime.fromisoformat(NOW))

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        row = {**graph_record(requested_pr(request)), field: value, "body": SECRET_PAYLOAD_MARKER}
        return graph(row)

    async with (
        running(tmp_path, clock) as (ledger, store),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client,
    ):
        await seed(ledger, 1)
        cursor = await ledger.fetch_cursor(REPO, PULL_REQUESTS)
        before = await audit(ledger, 1)
        await details(ledger, clock, monkeypatch).poll(client)
        row = await ledger.pull_request(REPO, 1)
        assert row is not None
        assert row.merge_commit_sha is None
        assert await audit(ledger, 1) == before
        assert await ledger.fetch_cursor(REPO, PULL_REQUESTS) == cursor
        assert await store.pending_action() is None
        assert SECRET_PAYLOAD_MARKER not in caplog.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("number", 2),
        ("base", {"repo": {"full_name": "wrong/repo"}}),
        ("head", {"sha": OTHER_SHA}),
        ("merged", False),
        ("state", "open"),
        ("merged_at", None),
        ("merge_commit_sha", None),
    ],
)
async def test_bad_rest_proof_leaves_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    clock = FixedClock(datetime.fromisoformat(NOW))

    def handle(request: httpx.Request) -> httpx.Response:
        pr = requested_pr(request)
        if request.method == "POST":
            return graph(graph_record(pr, None))
        return httpx.Response(200, json={**rest(pr, MERGE_SHA), field: value})

    async with (
        running(tmp_path, clock) as (ledger, _),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client,
    ):
        await seed(ledger, 1)
        before = await audit(ledger, 1)
        await details(ledger, clock, monkeypatch).poll(client)
        assert await audit(ledger, 1) == before
        pull = await ledger.pull_request(REPO, 1)
        assert pull is not None
        assert pull.merge_commit_sha is None


async def test_bounded_attempts_rotate_across_restart_and_honor_cooldown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Provider timestamps are ahead of the local process clock throughout the test.
    clock = FixedClock(datetime.fromisoformat("2026-11-27T09:00:00Z"))
    calls: list[tuple[str, int]] = []
    limited = False
    available = False

    def handle(request: httpx.Request) -> httpx.Response:
        pr = requested_pr(request)
        calls.append((request.method, pr))
        if limited:
            return httpx.Response(
                200,
                headers={"Retry-After": "10"},
                json={
                    "errors": [{"type": "RATE_LIMITED", "message": SECRET_PAYLOAD_MARKER}],
                },
            )
        if request.method == "POST":
            return graph(graph_record(pr, MERGE_SHA if available and pr == 1 else None))
        return httpx.Response(200, json=rest(pr, None))

    async with (
        running(tmp_path, clock) as (ledger, _),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client,
    ):
        await seed(ledger, 1, 2, 3, 4, 5)
        await details(ledger, clock, monkeypatch).poll(client)
        assert calls == [("POST", 1), ("GET", 1), ("POST", 2), ("GET", 2)]

    async with (
        running(tmp_path, clock) as (ledger, _),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client,
    ):
        poller = details(ledger, clock, monkeypatch)
        limited = True
        await poller.poll(client)
        assert calls[-1] == ("POST", 3)
        retry = await ledger.fetch_retry(REPO, DETAILS)
        assert retry is not None
        call_count = len(calls)
        limited = False
        await poller.poll(client)
        assert len(calls) == call_count
        clock.advance(11)
        await ledger.set_github_error(None)
        await poller.poll(client)
        assert 0 < len(calls) - call_count <= 4
        available = True
        for _ in range(4):
            call_count = len(calls)
            await poller.poll(client)
            assert len(calls) - call_count <= 4
        resolved = await ledger.pull_request(REPO, 1)
        assert resolved is not None
        assert resolved.merge_commit_sha == MERGE_SHA
        assert {pr for _, pr in calls} == {1, 2, 3, 4, 5}


async def test_concurrent_and_later_evidence_preserves_established_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FixedClock(datetime.fromisoformat(NOW))

    async with running(tmp_path, clock) as (ledger, store):
        await seed(ledger, 1)

        async def handle(request: httpx.Request) -> httpx.Response:
            # A normal list result wins the race while the backfill is in flight.
            assert requested_pr(request) == 1
            await poll_pulls(ledger, merged(1, MERGE_SHA))
            return graph(graph_record(1, OTHER_SHA))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await details(ledger, clock, monkeypatch).poll(client)
        before = await audit(ledger, 1)
        await poll_pulls(ledger, merged(1, OTHER_SHA))
        row = await ledger.pull_request(REPO, 1)
        assert row is not None
        assert row.merge_commit_sha == MERGE_SHA
        assert before == await audit(ledger, 1)
        assert [fields["merge_sha"] for kind, fields in before if kind == MERGE_OBSERVED] == [
            MERGE_SHA
        ]
        assert await store.pending_action() is None


@pytest.mark.parametrize("invalid", ["partial", "oversized", "invalid-json", "unavailable"])
async def test_failed_read_can_recover_next_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    from plotroom.adapters.github import read as github_read

    monkeypatch.setattr(github_read, "MAX_RESPONSE", 1024)
    clock = FixedClock(datetime.fromisoformat(NOW))
    successful = False

    def handle(request: httpx.Request) -> httpx.Response:
        number = requested_pr(request)
        assert request.method == "POST"
        if successful:
            return graph(graph_record(number))
        if invalid == "partial":
            body = graph(graph_record(number)).json()
            body["errors"] = [{"message": SECRET_PAYLOAD_MARKER}]
            return httpx.Response(200, json=body)
        if invalid == "oversized":
            return httpx.Response(200, content=b"x" * 1025)
        if invalid == "invalid-json":
            return httpx.Response(200, content=b'{"data":')
        return httpx.Response(503)

    async with (
        running(tmp_path, clock) as (ledger, store),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client,
    ):
        await seed(ledger, 1)
        before = await audit(ledger, 1)
        poller = details(ledger, clock, monkeypatch)
        await poller.poll(client)
        assert await audit(ledger, 1) == before
        successful = True
        await poller.poll(client)
        [result] = [fields for kind, fields in await audit(ledger, 1) if kind == MERGE_OBSERVED]
        assert result["merge_sha"] == MERGE_SHA
        assert await store.pending_action() is None


async def test_historical_row_backfills_quietly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FixedClock(datetime.fromisoformat(NOW))

    def handle(request: httpx.Request) -> httpx.Response:
        return graph(graph_record(requested_pr(request)))

    async with (
        running(tmp_path, clock) as (ledger, store),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client,
    ):
        await poll_pulls(ledger, merged(1))
        await poll_pulls(ledger, replace(opened(99), updated_at=NEWER))
        before = await audit(ledger, 1)
        await details(ledger, clock, monkeypatch).poll(client)
        row = await ledger.pull_request(REPO, 1)
        assert row is not None
        assert row.merge_commit_sha == MERGE_SHA
        assert await audit(ledger, 1) == before
        assert await store.pending_action() is None
