"""Deterministic acceptance exercises for the local GitHub projection."""

from pathlib import Path
from typing import Any
from unittest.mock import create_autospec

import httpx
import pytest

from fakes import FixedClock
from plotroom.adapters.github import (
    GitHubPoller,
    GitHubUnavailable,
    LocalGitHubCredential,
    PullRequestFetcher,
)
from plotroom.adapters.github.ci import fetch_main
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.pr_details import fetch_threads
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.work_items_github_issues import GitHubIssuesWorkItems
from plotroom.app.config import ConfigError, load_config
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError
from plotroom.core.github import DETAILS, PullRequest
from plotroom.core.github_projection import (
    CHECKS,
    COMMENT_BACKFILL,
    COMMENTS,
    FILES,
    ISSUE_TREE,
    LABELS,
    MAIN,
    THREADS,
    latest_checks,
    main_verdict,
    unresolved_threads,
)
from plotroom.core.ledger import Ledger
from plotroom.core.merge_gate import GatePolicy
from plotroom.ports.github_write import BranchUpdateReceipt, FixtureGitHubWrite

REPO = "fixture/repo"
API = "https://api.github.com"
FIRST = "1" * 40
SECOND = "2" * 40
MAIN_SHA = "3" * 40
PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-11-22T12:00:00Z"
LATER = "2026-11-22T12:00:02Z"
BODY = "  café \r\n\timmutable wording \u2603\n"


def status(n: int, name: str, conclusion: str | None) -> dict[str, Any]:
    return {
        "id": n,
        "name": name,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
    }


class Fixture:
    def __init__(self) -> None:
        self.step = 0
        self.break_threads = False
        self.move_thread_head = False
        self.change_thread_count = False
        self.duplicate_thread_id = False
        self.overstate_thread_count = False
        self.resolve_last_thread = False
        self.first_thread_page_size = 1
        self.limit = False
        self.extra_comment = False
        self.reset_check = False
        self.requests: list[str] = []

    @property
    def sha(self) -> str:
        return SECOND if self.step >= 2 else FIRST

    def threads(self, request: httpx.Request) -> httpx.Response:
        import json

        assert request.method == "POST"
        query = json.loads(request.content)
        assert query["query"].lstrip().startswith("query PullReviewThreads")
        after = query["variables"]["after"]
        if after and self.break_threads:
            return httpx.Response(503)
        all_nodes = [
            {
                "id": "first" if i == 0 else f"extra-{i}",
                "isResolved": self.step > 0,
                "path": "old.py",
                "line": None,
            }
            for i in range(self.first_thread_page_size)
        ]
        all_nodes.append(
            {
                "id": "first" if self.duplicate_thread_id else "second",
                "isResolved": self.resolve_last_thread,
                "path": "old.py",
                "line": None,
            }
        )
        connection = {
            "nodes": all_nodes[-1:] if after else all_nodes[:-1],
            "totalCount": len(all_nodes)
            + int(self.overstate_thread_count)
            + int(bool(after) and self.change_thread_count),
            "pageInfo": {"endCursor": "next", "hasNextPage": not after},
        }
        head = SECOND if after and self.move_thread_head else self.sha
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {"pullRequest": {"headRefOid": head, "reviewThreads": connection}}
                }
            },
        )

    @staticmethod
    def issue(number: int, *labels: str) -> dict[str, Any]:
        return {
            "number": number,
            "state": "open",
            "state_reason": None,
            "title": f"Ticket {number}",
            "body": "",
            "milestone": {"number": 1},
            "assignees": [],
            "labels": [{"name": label} for label in labels],
            "updated_at": NOW,
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer fixture"
        path = request.url.path
        self.requests.append(path)
        if path == "/graphql":
            return self.threads(request)
        assert request.method == "GET"
        path = path.removeprefix(f"/repos/{REPO}")
        if self.limit and path == "":
            return httpx.Response(429, headers={"Retry-After": "5"})
        pr = {
            "number": 4,
            "state": "open",
            "updated_at": LATER if self.step >= 2 else NOW,
            "head": {"ref": "worker", "sha": self.sha, "repo": {"full_name": REPO}},
            "merged_at": None,
            "body": "",
            "changed_files": 1,
        }
        check = status(15, "lint", "success" if self.step > 0 and not self.reset_check else None)
        rerun = status(16, "lint", "failure")
        run = {
            **status(50, "ci", "success" if self.step == 0 else None),
            "head_sha": MAIN_SHA,
            "run_attempt": 1 if self.step == 0 else 2,
            "created_at": NOW,
        }
        issue = {
            "number": 9,
            "state": "closed" if self.step >= 2 else "open",
            "state_reason": "completed" if self.step >= 2 else None,
            "title": "Inspect comments",
            "body": "Issue body",
            "milestone": {"number": 1},
            "assignees": [{"login": "editor"}] if self.step > 0 else [],
            "labels": [{"name": "story"}, {"name": "backlog"}],
            "updated_at": LATER if self.step > 0 else NOW,
        }
        result: dict[str, Any] = {
            "": {"default_branch": "trunk"},
            "/branches/trunk": {"commit": {"sha": MAIN_SHA}},
            "/actions/runs": {"workflow_runs": [run]},
            "/actions/runs/50/attempts/1": {
                **status(50, "ci", "success"),
                "head_sha": MAIN_SHA,
                "run_attempt": 1,
                "created_at": NOW,
            },
            "/actions/runs/50/attempts/1/jobs": {"jobs": [status(70, "image", "success")]},
            "/actions/runs/50/attempts/2/jobs": {"jobs": [status(71, "image", None)]},
            "/pulls": [pr],
            "/pulls/4": pr,
            "/pulls/4/files": [
                {"filename": "new.py", "status": "renamed", "previous_filename": "old.py"}
                if self.step >= 2
                else {"filename": "old.py", "status": "modified"}
            ],
            "/issues/4/events": [
                {
                    "id": 40,
                    "event": "labeled",
                    "actor": {"login": "editor"},
                    "label": {"name": "audited"},
                }
            ]
            if self.step > 0
            else [],
            "/issues/4/labels": [{"name": "audited"}] if self.step > 0 else [],
            "/issues": [issue, {"number": 4, "pull_request": {}}],
            "/issues/comments": [
                {
                    "id": 99,
                    "issue_url": f"{API}/repos/{REPO}/issues/9",
                    "created_at": LATER,
                    "updated_at": LATER,
                    "user": {"login": "editor"},
                    "body": BODY,
                },
            ]
            if self.step >= 1
            else [],
            "/milestones": [{"number": 1, "state": "open", "title": "plan"}],
            "/issues/9/sub_issues": [],
            "/issues/9/dependencies/blocked_by": [],
            f"/commits/{FIRST}/check-runs": {
                "check_runs": [check, rerun] if self.step > 0 else [check],
                "total_count": 2 if self.step > 0 else 1,
            },
            f"/commits/{SECOND}/check-runs": {
                "check_runs": [status(17, "lint", "success")],
                "total_count": 1,
            },
            f"/commits/{MAIN_SHA}/check-runs": {
                "check_runs": [status(72, "image", "success")],
                "total_count": 1,
            },
        }
        assert path in result, path
        if path == "/actions/runs":
            assert request.url.params["branch"] == "trunk"
        if path == "/issues/comments" and self.extra_comment and "since" in request.url.params:
            since = request.url.params["since"]
            assert since < LATER
            result[path].append({**result[path][0], "id": 100})
        return httpx.Response(200, json=result[path])


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ("move_thread_head", "PR head moved"),
        ("change_thread_count", "inconsistent review thread count"),
        ("duplicate_thread_id", "invalid review thread ID"),
        ("overstate_thread_count", "incomplete review thread list"),
    ],
)
async def test_thread_pages_require_consistent_head_and_exact_count(fault: str, error: str) -> None:
    fixture = Fixture()
    setattr(fixture, fault, True)
    pull = PullRequest(REPO, 4, "worker", FIRST, REPO, "open", NOW, NOW, True, 1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        with pytest.raises(GitHubUnavailable, match=error):
            await fetch_threads(GitHubReadClient(API, REPO), client, "fixture", pull)
    assert fixture.requests == ["/graphql", "/graphql"]


async def test_github_transitions_and_pagination_are_durable(tmp_path: Path) -> None:
    provider = Fixture()
    clock = FixedClock()
    credential = LocalGitHubCredential("TOKEN", {"TOKEN": "fixture"}, None)
    client_api = GitHubReadClient(API, REPO)
    workers = frozenset({"worker"})
    catalog = build_catalog(workers, github_repository=REPO)
    store = SqliteStore("demo", tmp_path)
    await store.migrate()

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handle)) as client:
        ledger = Ledger(
            store, catalog, clock, github_repository=REPO, merge_gate_policy=GatePolicy(1)
        )
        ledger.start()
        puller = GitHubPoller(ledger, clock, REPO, credential, PullRequestFetcher(API, REPO), 1)
        details = GitHubDetailPoller(
            ledger, clock, client_api, credential, ("image",), include_issue_tree=True
        )
        try:
            await ledger.execute(
                "orchestrator",
                "lane_start",
                "start",
                {"lane": "lane", "worker": "worker", "branch": "worker", "work_items": []},
            )
            await puller.poll(client)
            await details.poll(client)
            partial_fixture = await ledger.github_resource(REPO, THREADS, "4")
            assert partial_fixture is not None
            assert partial_fixture.complete
            assert partial_fixture.attributes["head_sha"] == FIRST
            assert (await store.read_merge_gate(REPO, 4)).threads == partial_fixture
            assert len(unresolved_threads(partial_fixture)) == 2
            provider.step = 1
            provider.break_threads = True
            await details.poll(client)
            partial = await ledger.github_resource(REPO, THREADS, "4")
            assert partial is not None
            assert not partial.complete
            assert partial.records == partial_fixture.records
            assert (await store.read_merge_gate(REPO, 4)).threads == partial
            assert len(unresolved_threads(partial)) == 2
            provider.break_threads = False
            provider.extra_comment = True
            await details.poll(client)
            main = await ledger.github_resource(REPO, MAIN)
            assert main is not None
            assert main.complete
            assert main.records[0].fields["attempt"] == 2
            verdict = main_verdict(main)
            assert verdict is not None
            assert verdict.fields["attempt"] == 1
            assert verdict.details["image_jobs"] == [status(70, "image", "success")]
            checks = await ledger.github_resource(REPO, CHECKS, FIRST)
            assert checks is not None
            assert latest_checks(checks)[0].fields["id"] == 16
            threads = await ledger.github_resource(REPO, THREADS, "4")
            assert threads is not None
            assert [r.key for r in unresolved_threads(threads)] == ["second"]
            labels = await ledger.github_resource(REPO, LABELS, "4")
            assert labels is not None
            assert labels.attributes["labels"] == {"audited": "editor"}

            provider.reset_check = True
            await details.poll(client)
            provider.step = 2
            await puller.poll(client)
            await details.poll(client)
            with pytest.raises(LedgerError, match="match the open GitHub projection"):
                await ledger.execute(
                    "worker:worker",
                    "lane_ready",
                    "stale",
                    {"lane": "lane", "pr": 4, "head_sha": FIRST},
                )
            files = await ledger.github_resource(REPO, FILES, "4")
            assert files is not None
            assert files.records[0].fields["previous_path"] == "old.py"
            issues = await ledger.github_resource(REPO, ISSUE_TREE)
            assert issues is not None
            assert issues.complete
            assert (await details.issue_tree())[1]["state"] == "closed"
            comments = await ledger.github_resource(REPO, COMMENTS, "99")
            assert comments is not None
            assert comments.records[0].key == "99"
            assert comments.records[0].body.encode() == BODY.encode()
            events = await store.events_after(0)
            kinds = [event.kind for event in events]
            assert kinds.count("github.review_thread") == 3
            check_ids = [event.fields["id"] for event in events if event.kind == "github.check_run"]
            assert check_ids.count(15) == 3
            assert check_ids.count(16) == 1
            assert kinds.count("github.pull_label") == 1
            assert kinds.count("github.pull_file") == 2
            comment_ids = [
                event.fields["id"] for event in events if event.kind == "github.issue_comment"
            ]
            assert comment_ids == [99, 100]
            for kind in ("issue_opened", "issue_assignees", "issue_closed"):
                assert kinds.count(f"github.{kind}") == 1
            assert all(
                event.lane_id == "lane"
                for event in events
                if event.fields.get("number") == 4 and event.source == "github"
            )
            check_evidence = await ledger.github_resource(REPO, CHECKS, SECOND)
            assert check_evidence is not None
            assert check_evidence.complete
            assert ledger.github_error is None
            provider.limit = True
            await details.poll(client)
            assert await ledger.fetch_retry(REPO, DETAILS) is not None
            assert (await ledger.merge_gate("clerk", 4)).reasons[0].code == "pr_unavailable"
            assert await ledger.github_resource(REPO, CHECKS, SECOND) == check_evidence
            final_seq = await store.head_seq()
        finally:
            await ledger.stop()
            await store.close()

        reopened = SqliteStore("demo", tmp_path)
        await reopened.migrate()
        ledger = Ledger(reopened, catalog, clock, github_repository=REPO)
        ledger.start()
        details = GitHubDetailPoller(
            ledger, clock, client_api, credential, ("image",), include_issue_tree=True
        )
        try:
            provider.limit = False
            calls = len(provider.requests)
            await details.poll(client)
            assert len(provider.requests) == calls
            clock.advance(5)
            await details.poll(client)
            assert len(provider.requests) > calls
            assert await reopened.head_seq() == final_seq
            assert await ledger.fetch_retry(REPO, DETAILS) is None
        finally:
            await ledger.stop()
            await reopened.close()


def test_remote_work_item_config_requires_one_source(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    base = '[projects.demo]\nbmad_installation = "trusted"\n'
    github = '\n[projects.demo.github]\nrepository = "Fixture/Repo"\nimage_jobs = ["image"]\n'
    path.write_text(base + 'github_ticket_tree = "tickets/initiative"\n' + github)
    config = load_config(path, {})
    assert config.github_ticket_trees["demo"] == ("tickets/initiative", tmp_path / "trusted")
    assert config.github["demo"].image_jobs == ("image",)
    path.write_text(base + 'github_ticket_tree = "tickets"\nticket_tree = "local"\n' + github)
    with pytest.raises(ConfigError, match="one work-item source"):
        load_config(path, {})
    path.write_text(base + 'github_ticket_tree = "../tickets"\n' + github)
    with pytest.raises(ConfigError, match="repository-relative"):
        load_config(path, {})
    path.write_text(base + '[projects.demo.github_issues]\nstore_config = "store.toml"\n' + github)
    config = load_config(path, {})
    assert config.github_issue_sources["demo"].store_config == tmp_path / "store.toml"


async def test_details_stage_and_dispatch_pinned_fixture_update(tmp_path: Path) -> None:
    provider = Fixture()
    provider.step = 1
    provider.resolve_last_thread = True
    base_sha = "4" * 40
    clock = FixedClock()
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    ledger = Ledger(
        store,
        build_catalog(),
        clock,
        github_repository=REPO,
        merge_gate_policy=GatePolicy(1, ci_workflow="ci.yml"),
    )
    ledger.start()
    credential = LocalGitHubCredential("TOKEN", {"TOKEN": "fixture"}, None)
    port = create_autospec(FixtureGitHubWrite, instance=True)
    port.update_branch.return_value = BranchUpdateReceipt("updated", MAIN_SHA)
    runtime = ProjectRuntime(
        ledger, clock, tick_seconds=5, action_attempts=3, fixture_github_write=port
    )
    comparisons: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        if path == "/pulls/4":
            pull: dict[str, Any] = provider.handle(request).json()
            pull["base"] = {"ref": "trunk", "repo": {"full_name": REPO}}
            return httpx.Response(200, json=pull)
        if path == "/actions/workflows/ci.yml/runs":
            run = {
                **status(50, "ci", "success"),
                "head_sha": MAIN_SHA,
                "head_branch": "trunk",
                "run_attempt": 1,
                "created_at": NOW,
                "workflow_id": 20,
            }
            return httpx.Response(200, json={"total_count": 1, "workflow_runs": [run]})
        if path == "/actions/runs/50/attempts/1/jobs":
            jobs = [status(70, "image", "success")]
            return httpx.Response(200, json={"total_count": 1, "jobs": jobs})
        if path == f"/commits/{FIRST}/check-runs":
            return httpx.Response(
                200, json={"total_count": 1, "check_runs": [status(15, "lint", "success")]}
            )
        if path.startswith("/compare/"):
            comparisons.append(path)
            left, _, right = path.removeprefix("/compare/").partition("...")
            assert (left, right) in {(FIRST, MAIN_SHA), (base_sha, FIRST), (base_sha, MAIN_SHA)}
            return httpx.Response(
                200,
                json={
                    "base_commit": {"sha": left},
                    "merge_base_commit": {"sha": base_sha},
                    "status": "ahead" if left == base_sha else "diverged",
                    "files": [{"filename": "old.py", "status": "modified"}],
                },
            )
        return provider.handle(request)

    try:
        pulls = GitHubPoller(ledger, clock, REPO, credential, PullRequestFetcher(API, REPO), 1)
        details = GitHubDetailPoller(
            ledger,
            clock,
            GitHubReadClient(API, REPO),
            credential,
            ci_workflow="ci.yml",
            on_merge_evidence=runtime.stage_branch_update,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await pulls.poll(client)
            await details.poll(client)
            await details.poll(client)
        assert len(comparisons) == 3
        assert (await ledger.merge_gate("clerk", 4)).reasons[0].code == "stale_ci"
        action_key = f"branch_update:4:{FIRST}"
        assert (action := await ledger.pending_action()) is not None
        assert action.key == action_key
        port.update_branch.assert_not_awaited()
        assert await runtime.run_one_action()
        assert not await runtime.run_one_action()
        port.update_branch.assert_awaited_once_with(
            repository=REPO,
            number=4,
            action_key=action_key,
            expected_head_sha=FIRST,
            expected_main_sha=MAIN_SHA,
            recovery_only=False,
        )
        port.merge.assert_not_awaited()
    finally:
        await ledger.stop()
        await store.close()


async def test_github_issues_flow_through_the_runtime_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("plotroom.adapters.github.issue_details.MAX_RELATION_REFRESHES", 2)

    class IssueTreeFixture(Fixture):
        def handle(self, request: httpx.Request) -> httpx.Response:
            root = f"/repos/{REPO}"
            path = request.url.path
            if path == f"{root}/issues":
                return httpx.Response(
                    200,
                    json=[
                        self.issue(7, "epic", "in-progress"),
                        self.issue(8, "story", "review"),
                        self.issue(9, "story", "backlog"),
                        self.issue(10, "story", "backlog"),
                    ],
                )
            if path.startswith(f"{root}/issues/"):
                n, suffix = path.removeprefix(f"{root}/issues/").split("/", 1)
                if suffix == "sub_issues":
                    relations = [9, 10, 8] if n == "7" else []
                elif suffix == "dependencies/blocked_by":
                    relations = {"9": [8], "10": [9]}.get(n, [])
                else:
                    return super().handle(request)
                return httpx.Response(
                    200,
                    json=[
                        {"number": number, "repository_url": f"{API}{root}"} for number in relations
                    ],
                )
            return super().handle(request)

    fixture = IssueTreeFixture()
    clock = FixedClock()
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    ledger = Ledger(store, build_catalog(github_repository=REPO), clock, github_repository=REPO)
    ledger.start()
    config = tmp_path / "tickets.toml"
    config.write_text(f'[tickets]\nstore = "github"\nrepo = "{REPO}"\n')
    starter = PROJECT_ROOT / ".agents/skills/bmad-agent-po/config/github-ticketing.toml"
    details = GitHubDetailPoller(
        ledger,
        clock,
        GitHubReadClient(API, REPO),
        LocalGitHubCredential("TOKEN", {"TOKEN": "fixture"}, None),
        include_issue_tree=True,
    )
    runtime = ProjectRuntime(
        ledger,
        clock,
        tick_seconds=1,
        action_attempts=1,
        work_items=GitHubIssuesWorkItems(details.issue_tree, starter, config, REPO),
    )
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            await details.poll(client)
            clock.advance(1)
            assert await runtime.tick_if_due()
            assert await ledger.ready_work_items() == ()
            await details.poll(client)
        clock.advance(1)
        assert await runtime.tick_if_due()
        assert [item.ref for item in await ledger.ready_work_items()] == ["9"]
    finally:
        await ledger.stop()
        await store.close()


async def test_main_verdict_survives_cold_start_mid_rerun() -> None:
    fixture = Fixture()
    fixture.step = 1
    api = GitHubReadClient(API, REPO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        main = await fetch_main(api, client, "fixture", ("image",), None)
    assert main.records[0].fields["attempt"] == 2
    verdict = main_verdict(main)
    assert verdict is not None
    assert verdict.fields["attempt"] == 1
    assert verdict.details["image_jobs"][0]["conclusion"] == "success"


async def test_issue_comment_backfill_advances_beyond_foreground_page_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("plotroom.adapters.github.read.MAX_PAGES", 1)

    class CommentHistory(Fixture):
        def handle(self, request: httpx.Request) -> httpx.Response:
            if request.url.path != f"/repos/{REPO}/issues/comments":
                return super().handle(request)
            sort = request.url.params["sort"]
            page = int(request.url.params["page"])
            identifier = 100 + (6 if sort == "updated" else page)
            has_next = ("since" not in request.url.params and sort == "updated") or (
                sort == "created" and page < 6
            )
            link = (
                {"Link": f'<{request.url.copy_set_param("page", page + 1)}>; rel="next"'}
                if has_next
                else {}
            )
            return httpx.Response(
                200,
                headers=link,
                json=[
                    {
                        "id": identifier,
                        "issue_url": f"{API}/repos/{REPO}/issues/9",
                        "created_at": NOW,
                        "updated_at": LATER,
                        "user": {"login": "editor"},
                        "body": BODY,
                    }
                ],
            )

    fixture = CommentHistory()
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    ledger = Ledger(store, build_catalog(github_repository=REPO), clock, github_repository=REPO)
    ledger.start()
    details = GitHubDetailPoller(
        ledger,
        clock,
        GitHubReadClient(API, REPO),
        LocalGitHubCredential("TOKEN", {"TOKEN": "fixture"}, None),
    )
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            await details.poll(client)
            cursor = await ledger.fetch_cursor(REPO, COMMENT_BACKFILL)
            assert cursor is not None
            assert cursor.watermark == "5"
            assert await ledger.github_resource(REPO, COMMENTS, "106") is not None
            assert await ledger.github_resource(REPO, COMMENTS, "105") is None
            await details.poll(client)
        cursor = await ledger.fetch_cursor(REPO, COMMENT_BACKFILL)
        assert cursor is not None
        assert cursor.watermark == "done"
        assert await ledger.github_resource(REPO, COMMENTS, "105") is not None
        observed = [
            event for event in await store.events_after(0) if event.kind == "github.issue_comment"
        ]
        assert len(observed) == 6
        assert await ledger.github_resource(REPO, ISSUE_TREE) is None
    finally:
        await ledger.stop()
        await store.close()
