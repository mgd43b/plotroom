"""Epic 7 acceptance over localhost HTTP and stdio with GitHub read fixtures."""

import asyncio
import sqlite3
from contextlib import closing
from typing import Any, Literal
from unicodedata import normalize

import httpx
import pytest

from fakes import FixedClock
from plotroom.adapters.github import GitHubPoller, PullRequestFetcher
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.read import GitHubReadClient, Row
from plotroom.app import serve
from plotroom.app.config import load_config
from plotroom.core.ledger import Ledger
from plotroom.ports.search import SearchBackfill
from test_epic3_e2e import (
    API,
    AS_OF,
    CREDENTIAL,
    MAIN_SHA,
    REPO,
    LocalPlotroom,
    ToolCall,
    git_fixture,
    tools,
)
from test_epic3_e2e import local_plotroom as local_plotroom

pytestmark = pytest.mark.parametrize("transport", ["http", "stdio"])

HUMAN = "fixture-human"
ISSUE_URL = f"{API}/repos/{REPO}/issues"
RECENT = "2026-11-21T12:00:00Z"
LATER = "2026-11-22T08:00:00Z"
STALE = "2026-11-18T12:00:00Z"
GOAL_NAME = "Milestone 1 coverage"
FIRST_GOAL = (
    "every issue where state = open and milestone = 1 "
    "satisfies assigned_to human or blocked_by(assigned_to human)"
)


@pytest.fixture
def queue_plotroom(local_plotroom: LocalPlotroom) -> LocalPlotroom:
    config = local_plotroom.config
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "[projects.demo]\n", f'[projects.demo]\nhuman_login = "{HUMAN}"\nask_stale_days = 3\n'
        ),
        encoding="utf-8",
    )
    return local_plotroom


def issue(
    number: int,
    *,
    state: Literal["open", "closed"] = "open",
    assignee: str | None = None,
    comments: int = 0,
    milestone: int = 1,
    updated_at: str = LATER,
) -> Row:
    return {
        "number": number,
        "state": state,
        "title": f"Fixture issue {number}",
        "body": "Fixture issue description",
        "updated_at": updated_at,
        "comments": comments,
        "milestone": {"number": milestone},
        "assignees": [{"login": assignee}] if assignee is not None else [],
        "labels": [{"name": "planning"}],
    }


def comment(number: int, comment_id: int, body: str, author: str, created_at: str) -> Row:
    return {
        "id": comment_id,
        "issue_url": f"{ISSUE_URL}/{number}",
        "created_at": created_at,
        "updated_at": created_at,
        "body": body,
        "user": {"login": author},
    }


def blocker(number: int, repository: str = REPO) -> Row:
    return {"number": number, "repository_url": f"{API}/repos/{repository}"}


async def observe_github(
    ledger: Ledger,
    issues: list[Row],
    comments: tuple[Row, ...] = (),
    *,
    blockers: dict[int, list[Row]] | None = None,
) -> None:
    routes: dict[str, Any] = {
        "": {"default_branch": "main"},
        "/branches/main": {"commit": {"sha": MAIN_SHA}},
        "/pulls": [],
        "/actions/runs": {"workflow_runs": []},
        "/issues": issues,
        "/issues/comments": comments,
        "/deployments": [],
        "/milestones": [
            {"number": number, "state": "open", "title": f"Milestone {number}"} for number in (1, 2)
        ],
        f"/commits/{MAIN_SHA}/check-runs": {"total_count": 0, "check_runs": []},
    }
    for row in issues:
        number: int = row["number"]
        routes[f"/issues/{number}"] = row
        routes[f"/issues/{number}/comments"] = [
            entry for entry in comments if entry["issue_url"] == f"{ISSUE_URL}/{number}"
        ]
        routes[f"/issues/{number}/sub_issues"] = []
        routes[f"/issues/{number}/dependencies/blocked_by"] = (blockers or {}).get(number, [])
    clock = FixedClock()
    puller = GitHubPoller(ledger, clock, REPO, CREDENTIAL, PullRequestFetcher(API, REPO), 1)
    details = GitHubDetailPoller(ledger, clock, GitHubReadClient(API, REPO), CREDENTIAL)
    async with httpx.AsyncClient(transport=git_fixture(routes)) as client:
        assert await puller.poll(client)
        await details.poll(client)


async def test_decide_requires_verbatim_projected_human_words(
    queue_plotroom: LocalPlotroom, transport: str
) -> None:
    words = "  Use cafe\u0301 \u2014 untouched.\r\n\tNext line.  "
    citation = {"kind": "github_comment", "repository": REPO, "issue": 1, "comment_id": 101}
    arguments = {"who": HUMAN, "governs": [f"{REPO}#1"], "settles": []}
    async with (
        asyncio.timeout(15),
        queue_plotroom.serve() as (endpoint, ledger),
        tools(endpoint, transport) as call,
    ):
        await call(
            "decide",
            request_id="absent",
            exact_words=words,
            where_ref=citation,
            error="unavailable",
            **arguments,
        )
        await observe_github(
            ledger,
            [issue(1, comments=3)],
            (
                comment(1, 101, f"Decision begins here [{words}]", HUMAN.upper(), RECENT),
                comment(1, 102, words, "fixture-orchestrator", LATER),
                comment(1, 103, "A different human decision.", HUMAN, LATER),
            ),
        )
        await call(
            "decide",
            request_id="wrong-author",
            exact_words=words,
            where_ref={**citation, "comment_id": 102},
            error="forbidden",
            **arguments,
        )
        rejected = await call(
            "decide",
            request_id="normalized",
            exact_words=normalize("NFC", words),
            where_ref=citation,
            error="invalid",
            **arguments,
        )
        assert "verbatim" in rejected["message"]
        wrong_comment = await call(
            "decide",
            request_id="wrong-comment",
            exact_words=words,
            where_ref={**citation, "comment_id": 103},
            error="invalid",
            **arguments,
        )
        assert "verbatim" in wrong_comment["message"]
        assert (await call("decisions"))["decisions"] == []
        created = await call(
            "decide", request_id="verbatim", exact_words=words, where_ref=citation, **arguments
        )
        [recorded] = (await call("decisions"))["decisions"]
        assert recorded["decision_id"] == created["decision_id"]
        assert recorded["where_ref"] == citation
        assert recorded["who"] == HUMAN
        assert recorded["exact_words"].encode("utf-8") == words.encode("utf-8")


async def ask_items(call: ToolCall) -> dict[tuple[str, str], dict[str, Any]]:
    items: dict[tuple[str, str], dict[str, Any]] = {}
    offset = 0
    while True:
        page = await call("next", offset=offset)
        for entry in page["items"]:
            if entry["kind"].startswith("ask_"):
                item = await call("next", item=entry["id"])
                key = (item["kind"], item["subject_ref"])
                assert key not in items
                items[key] = item["context"]
        if "next_offset" not in page:
            return items
        offset = page["next_offset"]


async def test_next_flags_each_ask_condition_in_isolation(
    queue_plotroom: LocalPlotroom, transport: str
) -> None:
    asks: tuple[tuple[int, int, str, list[str], str, str], ...] = (
        (1, 101, "Which branch should we use?", ["main", "release"], RECENT, "ask_buried"),
        (2, 201, "How should the note read?", [], RECENT, "ask_optionless"),
        (3, 301, "Should this wait?", ["wait", "proceed"], STALE, "ask_stale"),
        (4, 401, "Which title?", ["short", "long"], RECENT, "ask_orphaned"),
    )
    comments = tuple(
        comment(number, comment_id, text, "fixture-orchestrator", posted)
        for number, comment_id, text, _, posted, _ in asks
    )
    issues = [issue(number, comments=1) for number in (1, 2, 3, 4)]
    async with (
        asyncio.timeout(15),
        queue_plotroom.serve() as (endpoint, ledger),
        tools(endpoint, transport) as call,
    ):
        await observe_github(ledger, issues, comments)
        assert await ask_items(call) == {}
        expected: dict[tuple[str, str], dict[str, Any]] = {}
        for number, comment_id, text, options, _, kind in asks:
            result = await call(
                "ask",
                request_id=f"ask-{number}",
                issue=number,
                comment_id=comment_id,
                text=text,
                options=options,
            )
            ask_id = result["ask_id"]
            expected[kind, ask_id] = {
                "ask_id": ask_id,
                "repository": REPO,
                "issue": number,
                "comment_id": comment_id,
            }
        before_burial = await ask_items(call)
        assert before_burial == {
            key: context
            for key, context in expected.items()
            if key[0] in {"ask_optionless", "ask_stale"}
        }
        # Four days exceeds the configured three-day threshold, but not the seven-day default.
        issues[0] = issue(1, comments=2, updated_at=AS_OF)
        comments = (
            *comments,
            comment(1, 102, "Unrelated progress update", "fixture-worker", LATER),
        )
        await observe_github(ledger, issues, comments)
        assert await ask_items(call) == {
            key: context for key, context in expected.items() if key[0] != "ask_orphaned"
        }
        issues[-1] = issue(4, state="closed", comments=1, updated_at=AS_OF)
        await observe_github(ledger, issues, comments)
        assert await ask_items(call) == expected


def remaining(number: int, source: str, blocker_status: str = "failed") -> dict[str, Any]:
    assigned = "assigned_to human"
    blocked = "blocked_by(assigned_to human)"
    return {
        "issue": number,
        "title": f"Fixture issue {number}",
        "clauses": [
            {"clause": assigned, "position": source.index(assigned), "status": "failed"},
            {"clause": blocked, "position": source.index(blocked), "status": blocker_status},
        ],
    }


async def test_goal_reports_exact_remaining_issues_and_all_failed_clauses(
    queue_plotroom: LocalPlotroom, transport: str
) -> None:
    issues = [
        issue(8, state="closed"),
        issue(7, milestone=2),
        issue(6),
        issue(5, state="closed", assignee=HUMAN),
        issue(4, assignee="fixture-worker"),
        issue(3),
        issue(2),
        issue(1, assignee=HUMAN.upper()),
    ]
    blockers = {2: [blocker(1)], 3: [blocker(5)], 6: [blocker(2)]}
    async with (
        asyncio.timeout(15),
        queue_plotroom.serve() as (endpoint, ledger),
        tools(endpoint, transport) as call,
    ):
        await call("goal_set", request_id="first-goal", name=GOAL_NAME, text=FIRST_GOAL)
        await observe_github(ledger, issues, blockers=blockers)
        result = await call("goal", name=GOAL_NAME)
        assert result["done"] is False
        [report] = result["goals"]
        assert report["done"] is False
        assert report["remaining"] == [remaining(number, FIRST_GOAL) for number in (3, 4, 6)]
        issues = [
            {**row, "assignees": [{"login": HUMAN}], "updated_at": AS_OF}
            if row["number"] in (3, 4, 6)
            else row
            for row in issues
        ]
        await observe_github(ledger, issues, blockers=blockers)
        passed = await call("goal", name=GOAL_NAME)
        assert passed["done"] is True
        assert passed["goals"][0]["remaining"] == []


async def test_goal_survives_restart_and_reads_without_writing_with_uncertain_evidence(
    queue_plotroom: LocalPlotroom, transport: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "  " + FIRST_GOAL.replace(" satisfies", "\r\n\tsatisfies") + "  "
    data_dir = load_config(queue_plotroom.config, {}).data_dir
    backfill_complete = asyncio.Event()
    original_backfill = serve.backfill_search

    async def finish_backfill(ledger: Ledger, index: SearchBackfill) -> None:
        await original_backfill(ledger, index)
        backfill_complete.set()

    # Let the daemon finish unrelated startup writes before observing its database.
    monkeypatch.setattr(serve, "backfill_search", finish_backfill)
    async with (
        asyncio.timeout(15),
        queue_plotroom.serve() as (endpoint, _),
        tools(endpoint, transport, token=endpoint.worker) as worker,
        tools(endpoint, transport) as call,
    ):
        await backfill_complete.wait()
        await worker(
            "goal_set", request_id="unauthorized", name=GOAL_NAME, text=source, error="forbidden"
        )
        assert await call("goal_set", request_id="durable", name=GOAL_NAME, text=source) == {
            "name": GOAL_NAME
        }

    backfill_complete.clear()
    async with (
        asyncio.timeout(15),
        queue_plotroom.serve() as (endpoint, ledger),
        tools(endpoint, transport) as call,
    ):
        await backfill_complete.wait()
        issues = [issue(1, assignee=HUMAN), issue(2), issue(3)]
        # An identically numbered foreign blocker cannot prove coverage. Duplicate
        # relationship rows make issue 3's blocker evidence incomplete.
        blockers = {2: [blocker(1, "fixture/other")], 3: [blocker(1), blocker(1)]}
        await observe_github(ledger, issues, blockers=blockers)
        db_uri = (data_dir / "demo.db").as_uri() + "?mode=ro"
        with closing(sqlite3.connect(db_uri, uri=True)) as observer:
            cursor = await ledger.head_seq()
            version = observer.execute("PRAGMA data_version").fetchone()
            assert version is not None
            result = await call("goal", name=GOAL_NAME)
            assert await call("goal") == result
            assert result["done"] is False
            [report] = result["goals"]
            assert report["name"] == GOAL_NAME
            assert report["text"].encode("utf-8") == source.encode("utf-8")
            assert report["updated_at"] == AS_OF
            assert report["remaining"] == [
                remaining(number, source, "unavailable") for number in (2, 3)
            ]
            assert await ledger.head_seq() == cursor
            assert observer.execute("PRAGMA data_version").fetchone() == version

            # A repeated issue number invalidates the issue listing as a whole.
            await observe_github(ledger, [*issues, issues[0]], blockers=blockers)
            cursor = await ledger.head_seq()
            version = observer.execute("PRAGMA data_version").fetchone()
            assert version is not None
            await call("goal", name=GOAL_NAME, error="unavailable")
            assert await ledger.head_seq() == cursor
            assert observer.execute("PRAGMA data_version").fetchone() == version
