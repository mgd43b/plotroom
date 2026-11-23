"""Clerk Rule 1 over recorded check facts and a consistent SQLite read."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from fakes import FixedClock
from plotroom.adapters.github import GitHubUnavailable
from plotroom.adapters.github.ci import fetch_checks
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.adapters.store_sqlite import SqliteStore, github_sql
from plotroom.app.config import ConfigError, load_config
from plotroom.core import merge_gate
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError, format_time
from plotroom.core.github import PULL_REQUESTS, FetchCursor, PullRequest
from plotroom.core.github_projection import CHECKS, GitHubRecord, GitHubResource
from plotroom.core.ledger import Ledger
from plotroom.core.merge_gate import GatePolicy, GateSnapshot, evaluate_gate

REPOSITORY = "fixture/project"
NOW = datetime(2026, 11, 23, 12, tzinfo=UTC)
AS_OF = format_time(NOW - timedelta(seconds=10))
HEAD = "1" * 40
PULL = PullRequest(REPOSITORY, 4, "branch", HEAD, REPOSITORY, "open", AS_OF, AS_OF, True, 1)


def check(
    check_id: int,
    name: str,
    conclusion: str | None,
    head: str = HEAD,
    *,
    app_id: int | None = 1,
) -> GitHubRecord:
    return GitHubRecord(
        str(check_id),
        "check_run",
        {
            "id": check_id,
            "name": name,
            "head_sha": head,
            "status": "completed" if conclusion else "in_progress",
            "conclusion": conclusion,
        },
        details={"app_id": app_id} if app_id is not None else {},
    )


@pytest.mark.parametrize(
    ("records", "minimum", "ignored", "code", "exit_class", "detail"),
    [
        (
            (check(1, "lint", None), check(2, "test", "failure")),
            3,
            ("lint",),
            "checks_pending",
            2,
            "1 checks pending",
        ),
        (
            (check(1, "lint", "failure"), check(2, "lint", "success")),
            2,
            (),
            "checks_below_minimum",
            2,
            "only 1 checks have reported (at least 2 expected)",
        ),
        ((check(2, "lint", "success"), check(1, "lint", "failure")), 1, (), None, 0, ""),
        (
            (check(1, "lint", "failure", app_id=1), check(2, "lint", "success", app_id=2)),
            1,
            (),
            "checks_failing",
            2,
            "failing: lint",
        ),
        (
            (check(1, "lint", "success", app_id=1), check(2, "lint", "success", app_id=2)),
            2,
            (),
            "checks_below_minimum",
            2,
            "only 1 checks have reported (at least 2 expected)",
        ),
        (
            (check(1, "lint", None, app_id=1), check(2, "lint", "success", app_id=2)),
            1,
            ("lint",),
            "checks_pending",
            2,
            "1 checks pending",
        ),
        (
            (check(1, "lint", "failure", app_id=None), check(2, "lint", "success", app_id=None)),
            1,
            (),
            "checks_failing",
            2,
            "failing: lint",
        ),
        (
            (check(1, "lint", "failure", app_id=1), check(2, "lint", "success", app_id=None)),
            1,
            (),
            "checks_failing",
            2,
            "failing: lint",
        ),
        (
            (check(1, "lint", "success", app_id=None), check(2, "lint", "success", app_id=None)),
            2,
            (),
            "checks_below_minimum",
            2,
            "only 1 checks have reported (at least 2 expected)",
        ),
        (
            (
                check(1, "review", "failure"),
                check(2, "lint", "cancelled"),
                check(3, "test", "skipped"),
                check(4, "trace", "neutral"),
            ),
            4,
            ("review",),
            None,
            0,
            "",
        ),
        (
            (check(1, "review", "action_required"), check(2, "test", "timed_out")),
            2,
            ("Review",),
            "checks_failing",
            2,
            "failing: review; test",
        ),
        ((check(1, "lint", "stale"),), 1, (), "checks_pending", 2, "1 checks pending"),
        (
            (check(1, "lint", "failure"),),
            None,
            (),
            "minimum_checks_unconfigured",
            1,
            "set the minimum to at least one check",
        ),
        (
            (check(1, "lint", "unknown"),),
            1,
            (),
            "checks_unreadable",
            2,
            "could not read the checks",
        ),
        (
            (check(1, "lint", "success", "2" * 40),),
            1,
            (),
            "checks_unreadable",
            2,
            "could not read the checks",
        ),
    ],
)
def test_clerk_rule_1(
    monkeypatch: pytest.MonkeyPatch,
    records: tuple[GitHubRecord, ...],
    minimum: int | None,
    ignored: tuple[str, ...],
    code: str | None,
    exit_class: int,
    detail: str,
) -> None:
    monkeypatch.setattr(merge_gate, "GATE_RULES", (merge_gate._rule_checks,))  # pyright: ignore[reportPrivateUsage]
    snapshot = GateSnapshot(PULL, GitHubResource(records, as_of=format_time(NOW)))
    verdict = evaluate_gate(snapshot, GatePolicy(minimum, ignored), NOW)
    assert (verdict.head_sha, verdict.as_of, verdict.exit_class) == (HEAD, AS_OF, exit_class)
    assert verdict.verdict == ("MERGE" if code is None else "REFUSE")
    if code is None:
        assert not verdict.reasons
    else:
        [reason] = verdict.reasons
        assert (reason.code, reason.detail, reason.exit_class) == (code, detail, exit_class)


@pytest.mark.parametrize(
    ("snapshot", "code"),
    [
        (GateSnapshot(None, None), "pr_unavailable"),
        (GateSnapshot(replace(PULL, complete=False), None), "pr_incomplete"),
        (GateSnapshot(replace(PULL, state="merged"), None), "pr_not_open"),
        (GateSnapshot(replace(PULL, as_of="2026-11-23T11:55:00Z"), None), "pr_stale"),
        (GateSnapshot(PULL, None, FetchCursor("", "2026-11-23T11:55:00Z", 1)), "pr_stale"),
        (GateSnapshot(PULL, None, github_unavailable=True), "pr_unavailable"),
        (GateSnapshot(PULL, GitHubResource((), complete=False, as_of=AS_OF)), "checks_unreadable"),
        (GateSnapshot(PULL, GitHubResource((), as_of="2026-11-23T11:55:00Z")), "checks_stale"),
    ],
)
def test_projection_fails_closed(snapshot: GateSnapshot, code: str) -> None:
    result = evaluate_gate(snapshot, GatePolicy(1), NOW)
    assert result.reasons[0].code == code


async def test_provider_check_count_requires_a_complete_list() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/check-runs")
        return httpx.Response(
            200, json={"total_count": 2, "check_runs": [check(1, "test", "success").fields]}
        )

    api = GitHubReadClient("https://api.github.com", REPOSITORY)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(GitHubUnavailable, match="incomplete result count"):
            await fetch_checks(api, client, "fixture", HEAD)


def test_fresh_incremental_poll_can_cover_an_unchanged_pull() -> None:
    pull = replace(PULL, as_of="2026-11-23T11:55:00Z")
    checks = GitHubResource((check(1, "test", None),), as_of=AS_OF)
    policy = GatePolicy(1, pull_max_age=timedelta(minutes=60))
    with_cursor = GateSnapshot(pull, checks, FetchCursor(AS_OF, AS_OF, 2))
    assert evaluate_gate(with_cursor, policy, NOW).reasons[0].code == "checks_pending"
    assert evaluate_gate(replace(with_cursor, pull_cursor=None), policy, NOW).reasons[0].code == (
        "pr_stale"
    )


def test_expired_pull_is_included_in_reported_watermark() -> None:
    old = format_time(NOW - timedelta(minutes=5))
    recent = FetchCursor(AS_OF, AS_OF, 2)
    checks = GitHubResource((check(1, "test", "success"),), as_of=AS_OF)
    result = evaluate_gate(
        GateSnapshot(replace(PULL, as_of=old), checks, recent), GatePolicy(1), NOW
    )
    assert result.reasons[0].code == "pr_stale"
    assert result.as_of == old


async def test_merge_gate_reads_one_database_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SqliteStore("fixture", tmp_path)
    await store.migrate()
    original = github_sql.pull_request
    read_pull = asyncio.Event()
    writer_done = asyncio.Event()

    async def pause_after_pull(
        connection: AsyncConnection, repository: str, number: int
    ) -> PullRequest | None:
        pull = await original(connection, repository, number)
        read_pull.set()
        await writer_done.wait()
        return pull

    def observation(conclusion: str) -> GitHubResource:
        return GitHubResource((check(1, "test", conclusion),), as_of=AS_OF)

    try:
        async with store.transaction() as tx:
            await tx.save_pull_request(PULL)
            await tx.save_fetch_cursor(REPOSITORY, PULL_REQUESTS, FetchCursor(AS_OF, AS_OF, 1))
            await tx.save_github_resource(REPOSITORY, CHECKS, HEAD, observation("success"))
        monkeypatch.setattr(github_sql, "pull_request", pause_after_pull)
        async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
            reader = tasks.create_task(store.read_merge_gate(REPOSITORY, 4))
            await read_pull.wait()
            async with store.transaction() as tx:
                later = format_time(NOW)
                await tx.save_pull_request(replace(PULL, head_sha="2" * 40, as_of=later))
                await tx.save_fetch_cursor(REPOSITORY, PULL_REQUESTS, FetchCursor(later, later, 2))
                await tx.save_github_resource(REPOSITORY, CHECKS, HEAD, observation("failure"))
            writer_done.set()
        snapshot = reader.result()
        assert snapshot.pull == PULL
        assert snapshot.pull_cursor == FetchCursor(AS_OF, AS_OF, 1)
        assert snapshot.checks == observation("success")
        assert await store.head_seq() == 0
    finally:
        await store.close()


async def test_read_only_ledger_query_is_scoped_to_the_operator_roles(tmp_path: Path) -> None:
    store = SqliteStore("fixture", tmp_path)
    await store.migrate()
    ledger = Ledger(
        store,
        build_catalog(github_repository=REPOSITORY),
        FixedClock(NOW),
        github_repository=REPOSITORY,
        merge_gate_policy=GatePolicy(1),
    )
    try:
        async with store.transaction() as tx:
            await tx.save_pull_request(PULL)
            await tx.save_github_resource(
                REPOSITORY, CHECKS, HEAD, GitHubResource((check(1, "test", None),), as_of=AS_OF)
            )
        ledger.github_error = None
        for role in ("clerk", "orchestrator"):
            result = await ledger.merge_gate(role, 4)
            assert (result.head_sha, result.as_of, result.reasons[0].code) == (
                HEAD,
                AS_OF,
                "checks_pending",
            )
        with pytest.raises(LedgerError, match="only the clerk and orchestrator"):
            await ledger.merge_gate("worker:fixture", 4)
        ledger.github_error = "GitHub denied access to the configured repository"
        result = await ledger.merge_gate("clerk", 4)
        assert result.reasons[0].code == "pr_unavailable"
        assert result.head_sha == HEAD
        assert await store.head_seq() == 0
    finally:
        await store.close()


def test_project_policy_has_no_default_minimum_and_preserves_check_names(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    base = '[projects.fixture.github]\nrepository = "fixture/project"\npoll_seconds = 5\n'
    path.write_text(base, encoding="utf-8")
    assert load_config(path, {}).github["fixture"].merge_gate_policy.min_checks is None
    path.write_text(
        base + 'min_checks = 3\nignore_checks = [" Review ", "a,b"]\n', encoding="utf-8"
    )
    policy = load_config(path, {}).github["fixture"].merge_gate_policy
    assert (policy.min_checks, policy.ignore_checks, policy.max_age) == (
        3,
        (" Review ", "a,b"),
        timedelta(seconds=10),
    )
    path.write_text(base + "min_checks = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="min_checks must be an integer of at least 1"):
        load_config(path, {})
