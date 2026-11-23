"""Fixture evidence for the local human queue."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from fakes import FixedClock
from plotroom.adapters.github import LocalGitHubCredential
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.store_sqlite.ledger_sql import SqliteTransaction
from plotroom.core.ask_checks import ASK_BURIED, ASK_OPTIONLESS, ASK_ORPHANED, ASK_STALE
from plotroom.core.catalog import build_catalog
from plotroom.core.events import Event, LedgerError, NewEvent
from plotroom.core.github import apply_ask_comment_history, apply_comments
from plotroom.core.github_projection import (
    ASK_COMMENT_HISTORY,
    COMMENT_BACKFILL,
    COMMENTS,
    ISSUES,
    GitHubRecord,
    GitHubResource,
    apply_resource,
)
from plotroom.core.ledger import Ledger

REPO = "fixture/human-queue"
HUMAN = "fixture-human"
ORCHESTRATOR = "orchestrator"
STAMP = "2026-11-23T12:00:00.000000Z"
WORDS = "  Café\r\nChoose option A.  "


@asynccontextmanager
async def decision_ledger(
    directory: Path, human_login: str | None = HUMAN, *, clock: FixedClock | None = None
) -> AsyncGenerator[Ledger]:
    store = SqliteStore("demo", directory)
    await store.migrate()
    ledger = Ledger(
        store,
        build_catalog(github_repository=REPO, human_login=human_login),
        clock or FixedClock(),
        github_repository=REPO,
        human_login=human_login,
    )
    ledger.start()
    try:
        yield ledger
    finally:
        await ledger.stop()
        await store.close()


async def observe_comment(
    ledger: Ledger,
    *,
    author: str | None = HUMAN,
    issue: int = 42,
    body: str = WORDS,
    comment_id: int = 9001,
    stamp: str = STAMP,
) -> None:
    fields: dict[str, Any] = {
        "id": comment_id,
        "number": issue,
        "created_at": stamp,
        "updated_at": stamp,
    }
    if author is not None:
        fields["author"] = author
    comment = GitHubRecord(str(comment_id), "issue_comment", fields, body)
    cursor = await ledger.fetch_cursor(REPO, COMMENTS)
    await ledger.run(
        "system", lambda uow: apply_comments(uow, REPO, cursor, (comment,), stamp, stamp)
    )


def decision_args(**overrides: Any) -> dict[str, Any]:
    return {
        "who": HUMAN.upper(),
        "exact_words": WORDS,
        "where_ref": {
            "kind": "github_comment",
            "repository": REPO,
            "issue": 42,
            "comment_id": 9001,
        },
        "governs": ["fixture/human-queue#42", "7.1", "fixture/human-queue#42"],
        "settles": [],
        **overrides,
    }


async def test_projected_decision_round_trips_and_retry_keeps_receipt(tmp_path: Path) -> None:
    args = decision_args()
    async with decision_ledger(tmp_path) as ledger:
        await observe_comment(ledger, body=f"Before\n{WORDS}\nAfter")
        receipt = await ledger.execute(ORCHESTRATOR, "decide", "github-decision", args)
        decision_id = receipt.result["decision_id"]
        [record] = await ledger.list_decisions(ORCHESTRATOR, "fixture/human-queue#42")
        assert record == {
            **args,
            "decision_id": decision_id,
            "created_at": (await ledger.events_after(0))[-1].observed_at,
        }
        assert record["exact_words"].encode("utf-8") == WORDS.encode("utf-8")
        assert await ledger.list_decisions(ORCHESTRATOR, "fixture/human-queue#4") == []
        assert await ledger.list_decisions(ORCHESTRATOR, "Fixture/human-queue#42") == []
        assert await ledger.list_decisions(ORCHESTRATOR, "7.1") == [record]
        assert (
            len([event for event in await ledger.events_after(0) if event.kind == "core.decide"])
            == 1
        )

    async with decision_ledger(tmp_path) as ledger:
        replay = await ledger.execute(ORCHESTRATOR, "decide", "github-decision", args)
        assert replay.replayed
        assert replay.result == {"decision_id": decision_id}
        assert await ledger.list_decisions(ORCHESTRATOR, None) == [record]


async def test_decide_missing_comment_is_retryable_and_worker_is_refused(tmp_path: Path) -> None:
    args = decision_args()
    async with decision_ledger(tmp_path) as ledger:
        with pytest.raises(LedgerError) as worker:
            await ledger.execute("worker:fixture", "decide", "pending", args)
        assert worker.value.code == "forbidden"
        with pytest.raises(LedgerError) as missing:
            await ledger.execute(ORCHESTRATOR, "decide", "pending", args)
        assert missing.value.code == "unavailable"
        assert await ledger.head_seq() == 0
        assert await ledger.list_decisions(ORCHESTRATOR, None) == []
        with pytest.raises(LedgerError) as read:
            await ledger.list_decisions("worker:fixture", None)
        assert read.value.code == "forbidden"
        await observe_comment(ledger)
        assert not (await ledger.execute(ORCHESTRATOR, "decide", "pending", args)).replayed


@pytest.mark.parametrize(
    ("author", "issue", "overrides", "code"),
    [
        ("different-author", 42, {}, "forbidden"),
        (None, 42, {}, "forbidden"),
        (HUMAN, 42, {"who": "different-human"}, "forbidden"),
        (HUMAN, 43, {}, "invalid"),
        (HUMAN, 42, {"exact_words": "Use option B."}, "invalid"),
        (HUMAN, 42, {"exact_words": "Cafe\u0301"}, "invalid"),
        (HUMAN, 42, {"exact_words": WORDS.replace("\r\n", "\n")}, "invalid"),
        (
            HUMAN,
            42,
            {
                "where_ref": {
                    "kind": "github_comment",
                    "repository": "fixture/other",
                    "issue": 42,
                    "comment_id": 9001,
                }
            },
            "invalid",
        ),
        (
            HUMAN,
            42,
            {
                "where_ref": {
                    "kind": "github_comment",
                    "repository": REPO,
                    "issue": 42,
                    "comment_id": True,
                }
            },
            "invalid",
        ),
        (HUMAN, 42, {"where_ref": {"kind": "chat", "ref": "source", "issue": 42}}, "invalid"),
        (HUMAN, 42, {"where_ref": {"kind": "chat", "ref": 42}}, "invalid"),
        (HUMAN, 42, {"where_ref": {"kind": "chat", "ref": "source"}, "who": "other"}, "forbidden"),
    ],
)
async def test_decide_refuses_unverified_evidence(
    tmp_path: Path, author: str | None, issue: int, overrides: dict[str, Any], code: str
) -> None:
    async with decision_ledger(tmp_path) as ledger:
        await observe_comment(ledger, author=author, issue=issue)
        seq = await ledger.head_seq()
        with pytest.raises(LedgerError) as refusal:
            await ledger.execute(ORCHESTRATOR, "decide", "refusal", decision_args(**overrides))
        assert refusal.value.code == code
        assert await ledger.head_seq() == seq
        assert await ledger.list_decisions(ORCHESTRATOR, None) == []


async def test_chat_preserves_opaque_citation_without_human_config(tmp_path: Path) -> None:
    args = decision_args(
        who="Person From Chat",
        exact_words="\n\x00A\u0308, agreed.\r\n",
        where_ref={"kind": "chat", "ref": "  chat:fixture/é#message-2\t"},
    )
    async with decision_ledger(tmp_path, human_login=None) as ledger:
        await observe_comment(ledger)
        with pytest.raises(LedgerError) as denied:
            await ledger.execute(ORCHESTRATOR, "decide", "needs-human", decision_args())
        assert denied.value.code == "forbidden"
        receipt = await ledger.execute(ORCHESTRATOR, "decide", "chat-decision", args)
        [record] = await ledger.list_decisions(ORCHESTRATOR, None)
        assert record["decision_id"] == receipt.result["decision_id"]
        for key, value in args.items():
            assert record[key] == value
        assert record["exact_words"].encode("utf-8") == args["exact_words"].encode("utf-8")


async def test_failed_command_event_rolls_back_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with decision_ledger(tmp_path) as ledger:
        args = decision_args(where_ref={"kind": "chat", "ref": "fixture/message"})

        async def fail_insert(_transaction: SqliteTransaction, _event: Event) -> None:
            raise RuntimeError("synthetic command event failure")

        with monkeypatch.context() as patch:
            patch.setattr(SqliteTransaction, "insert_event", fail_insert)
            with pytest.raises(RuntimeError, match="synthetic command event failure"):
                await ledger.execute(ORCHESTRATOR, "decide", "atomic-decision", args)

        assert await ledger.head_seq() == 0
        assert await ledger.list_decisions(ORCHESTRATOR, None) == []
        assert not (await ledger.execute(ORCHESTRATOR, "decide", "atomic-decision", args)).replayed
        assert await ledger.head_seq() == 1


def ask_args(issue: int = 42, comment_id: int = 9001, **overrides: Any) -> dict[str, Any]:
    return {
        "issue": issue,
        "comment_id": comment_id,
        "text": WORDS,
        "options": ["  A  ", "B"],
        **overrides,
    }


async def issue_states(ledger: Ledger, states: dict[int, str], *, comment_count: int = 1) -> None:
    records = tuple(
        GitHubRecord(
            f"issue/{number}",
            "issue_opened" if state == "open" else "issue_closed",
            {"number": number, "state": state},
            details={"resource": "issue", "updated_at": STAMP, "comment_count": comment_count},
            fact=state,
        )
        for number, state in states.items()
    )
    await ledger.run(
        "system", lambda uow: apply_resource(uow, REPO, ISSUES, "", GitHubResource(records), STAMP)
    )


async def open_checks(ledger: Ledger) -> dict[tuple[str, str], str]:
    _, items = await ledger.read_next(ORCHESTRATOR, 0, 100)
    return {(item.kind, item.subject_ref): item.id for item in items}


async def test_ask_uses_exact_current_project_comment_and_retry_is_atomic(tmp_path: Path) -> None:
    async with decision_ledger(tmp_path) as ledger:
        args = ask_args()
        head = await ledger.head_seq()
        for role, call_args, code in (
            ("worker:fixture", args, "forbidden"),
            (ORCHESTRATOR, args, "unavailable"),
        ):
            with pytest.raises(LedgerError) as rejected:
                await ledger.execute(role, "ask", "retry-me", call_args)
            assert rejected.value.code == code
        assert await ledger.head_seq() == head
        assert await ledger.list_asks(ORCHESTRATOR, False) == []
        await observe_comment(ledger, author="fixture-agent", body=f"Opening\r\n{WORDS}\nEnd")
        with pytest.raises(LedgerError) as missing_issue:
            await ledger.execute(ORCHESTRATOR, "ask", "retry-me", args)
        assert missing_issue.value.code == "unavailable"
        await issue_states(ledger, {42: "open"})
        for bad_args in (ask_args(issue=43), ask_args(text="cafe"), ask_args(options=[None])):
            with pytest.raises(LedgerError) as rejected:
                await ledger.execute(ORCHESTRATOR, "ask", "retry-me", bad_args)
            assert rejected.value.code == "invalid"
        recorded = await ledger.execute(ORCHESTRATOR, "ask", "retry-me", args)
        [ask] = await ledger.list_asks(ORCHESTRATOR, False)
        assert ask == {
            **args,
            "repository": REPO,
            "ask_id": recorded.result["ask_id"],
            "comment_created_at": STAMP,
            "created_at": (await ledger.events_after(0))[-1].observed_at,
            "settled_by": None,
        }
        head = await ledger.head_seq()
        replay = await ledger.execute(ORCHESTRATOR, "ask", "retry-me", ask_args(issue=43))
        assert replay.replayed
        assert replay.result == recorded.result
        assert await ledger.head_seq() == head


async def test_ask_conditions_transition_once_on_updates_ticks_and_settlement(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
    async with decision_ledger(tmp_path, clock=clock) as ledger:
        await issue_states(ledger, {42: "open", 43: "open", 44: "open", 45: "closed"})
        ask_ids: dict[int, str] = {}
        for issue in (42, 43, 44, 45):
            comment_id = 9000 + issue
            stamp = "2026-11-16T12:00:00.000000Z" if issue == 44 else STAMP
            await observe_comment(
                ledger, issue=issue, comment_id=comment_id, author="fixture-agent", stamp=stamp
            )
            options = [] if issue == 43 else ["A", "B"]
            result = await ledger.execute(
                ORCHESTRATOR, "ask", str(issue), ask_args(issue, comment_id, options=options)
            )
            ask_ids[issue] = result.result["ask_id"]
        assert set(await open_checks(ledger)) == {(ASK_OPTIONLESS, ask_ids[43])}

        await observe_comment(
            ledger,
            issue=42,
            comment_id=10001,
            author="another-writer",
            stamp="2026-11-23T13:00:00.000000Z",
        )
        await ledger.run(
            "system",
            lambda uow: apply_comments(
                uow, REPO, None, (), "done", STAMP, cursor_resource=COMMENT_BACKFILL
            ),
        )
        assert (ASK_ORPHANED, ask_ids[45]) not in await open_checks(ledger)
        cached = await ledger.github_resource(REPO, COMMENTS, "9045")
        assert cached is not None
        await ledger.run(
            "system",
            lambda uow: apply_ask_comment_history(
                uow, REPO, 45, f"{STAMP}/1", cached.records, STAMP
            ),
        )
        clock.advance(86401)
        await ledger.append("system", NewEvent("core", "core.tick", "tick-1"))
        found = await open_checks(ledger)
        assert set(found) == {
            (ASK_BURIED, ask_ids[42]),
            (ASK_OPTIONLESS, ask_ids[43]),
            (ASK_STALE, ask_ids[44]),
            (ASK_ORPHANED, ask_ids[45]),
        }
        [stale] = await ledger.list_asks(ORCHESTRATOR, True)
        assert stale["ask_id"] == ask_ids[44]
        await ledger.append("system", NewEvent("core", "core.tick", "tick-2"))
        assert await open_checks(ledger) == found

        updated = await ledger.execute(
            ORCHESTRATOR, "ask", "options", ask_args(43, 9043, options=["A", "B"])
        )
        assert updated.result["ask_id"] == ask_ids[43]
        await observe_comment(
            ledger,
            issue=42,
            comment_id=10002,
            author=HUMAN.upper(),
            stamp="2026-11-23T14:00:00.000000Z",
        )
        await issue_states(ledger, {42: "open", 43: "open", 44: "open", 45: "open"})
        assert await open_checks(ledger) == {
            (ASK_STALE, ask_ids[44]): found[ASK_STALE, ask_ids[44]]
        }

        args = decision_args(
            where_ref={"kind": "chat", "ref": "fixture/message"},
            governs=[f"{REPO}#44"],
            settles=[ask_ids[44], "ask_missing"],
        )
        checkpoint = await ledger.head_seq()
        with pytest.raises(LedgerError) as rejected:
            await ledger.execute(ORCHESTRATOR, "decide", "settle", args)
        assert rejected.value.code == "not_found"
        assert await ledger.head_seq() == checkpoint
        assert await ledger.list_decisions(ORCHESTRATOR, None) == []
        assert await ledger.list_asks(ORCHESTRATOR, True) == [stale]

        args["settles"] = [ask_ids[44]]
        decided = await ledger.execute(ORCHESTRATOR, "decide", "settle", args)
        [decision] = await ledger.list_decisions(ORCHESTRATOR, f"{REPO}#44")
        assert decision["settles"] == [ask_ids[44]]
        linked = {
            row["ask_id"]: row["settled_by"] for row in await ledger.list_asks(ORCHESTRATOR, False)
        }
        assert linked[ask_ids[44]] == decision["decision_id"] == decided.result["decision_id"]
        assert await ledger.list_asks(ORCHESTRATOR, True) == []
        assert await open_checks(ledger) == {}
        await ledger.append("system", NewEvent("core", "core.tick", "tick-3"))
        assert await open_checks(ledger) == {}
        with pytest.raises(LedgerError) as rejected:
            await ledger.execute(ORCHESTRATOR, "decide", "other-decision", args)
        assert rejected.value.code == "conflict"


@pytest.mark.parametrize(
    "result",
    [
        "no-reply",
        "paginated-reply",
        "wrong-issue",
        "wrong-repo",
        "malformed",
        "failed-page",
        "shifted-page",
        "changed-issue",
    ],
)
async def test_closed_ask_requires_its_own_verified_comment_history(
    tmp_path: Path, result: str
) -> None:
    clock = FixedClock()
    pages: list[str] = []
    issue_url = f"https://api.github.com/repos/{REPO}/issues/42"

    def request_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path == f"/repos/{REPO}/issues/42":
            pages.append("confirm")
            return httpx.Response(
                200,
                json={
                    "number": 42,
                    "state": "closed",
                    "comments": 1 if result == "no-reply" else 2,
                    "updated_at": "2026-11-23T14:00:00Z" if result == "changed-issue" else STAMP,
                },
            )
        assert request.url.path == f"/repos/{REPO}/issues/42/comments"
        page = request.url.params["page"]
        pages.append(page)
        headers: dict[str, str] = {}
        reply: dict[str, Any] = {
            "id": 9002,
            "issue_url": issue_url,
            "body": "Human reply",
            "user": {"login": HUMAN},
            "created_at": "2026-11-23T13:00:00Z",
            "updated_at": "2026-11-23T13:00:00Z",
        }
        if page == "2":
            if result == "failed-page":
                return httpx.Response(503)
            if result == "shifted-page":
                return httpx.Response(200, json=[])
            if result == "wrong-issue":
                reply["issue_url"] = issue_url.removesuffix("42") + "43"
            if result == "wrong-repo":
                reply["issue_url"] = issue_url.replace(REPO, "unrelated/repo")
            if result == "malformed":
                reply["issue_url"] = "missing-path"
            return httpx.Response(200, json=[reply])
        if result != "no-reply":
            headers["Link"] = f'<{issue_url}/comments?page=2>; rel="next"'
        return httpx.Response(
            200,
            headers=headers,
            json=[
                {
                    "id": 9001,
                    "issue_url": issue_url,
                    "body": WORDS,
                    "user": {"login": "fixture-agent"},
                    "created_at": STAMP,
                    "updated_at": STAMP,
                }
            ],
        )

    async with decision_ledger(tmp_path, clock=clock) as ledger:
        await issue_states(ledger, {42: "closed"}, comment_count=1 if result == "no-reply" else 2)
        await observe_comment(ledger, author="fixture-agent")
        if result == "no-reply":
            await observe_comment(
                ledger, comment_id=9002, author=HUMAN, stamp="2026-11-23T13:00:00Z"
            )
        ask_id = (await ledger.execute(ORCHESTRATOR, "ask", "closed-ask", ask_args())).result[
            "ask_id"
        ]
        assert (ASK_ORPHANED, ask_id) not in await open_checks(ledger)
        issue_snapshot = await ledger.github_resource(REPO, ISSUES)
        assert issue_snapshot is not None
        poller = GitHubDetailPoller(
            ledger,
            clock,
            GitHubReadClient("https://api.github.com", REPO),
            LocalGitHubCredential("FIXTURE_TOKEN", {"FIXTURE_TOKEN": "fixture"}, None),
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(request_handler)) as client:
            await poller._poll_ask_histories(client, "fixture", issue_snapshot)  # pyright: ignore[reportPrivateUsage]

        expected_pages = ["1"] if result == "no-reply" else ["1", "2"]
        if result in {"no-reply", "paginated-reply", "changed-issue"}:
            expected_pages.append("confirm")
        assert pages == expected_pages
        checks = await open_checks(ledger)
        assert ((ASK_ORPHANED, ask_id) in checks) == (result == "no-reply")
        proof = await ledger.github_resource(REPO, ASK_COMMENT_HISTORY, "42")
        assert (proof is not None) == (result in {"no-reply", "paginated-reply"})
        if result == "no-reply":
            assert proof is not None
            assert proof.attributes["comment_ids"] == [9001]
            assert await ledger.github_resource(REPO, COMMENTS, "9002") is not None


async def test_ask_history_writer_rejects_proof_for_a_superseded_issue(tmp_path: Path) -> None:
    async with decision_ledger(tmp_path) as ledger:
        await issue_states(ledger, {42: "closed"})
        await observe_comment(ledger, author="fixture-agent")
        ask_id = (await ledger.execute(ORCHESTRATOR, "ask", "pending", ask_args())).result["ask_id"]
        cached = await ledger.github_resource(REPO, COMMENTS, "9001")
        assert cached is not None

        # A newer issue snapshot arrives after the provider confirmed the old history.
        await issue_states(ledger, {42: "closed"}, comment_count=2)
        await ledger.run(
            "system",
            lambda uow: apply_ask_comment_history(
                uow, REPO, 42, f"{STAMP}/1", cached.records, STAMP
            ),
        )

        assert await ledger.github_resource(REPO, ASK_COMMENT_HISTORY, "42") is None
        assert (ASK_ORPHANED, ask_id) not in await open_checks(ledger)
