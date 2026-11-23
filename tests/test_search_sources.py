"""Search real local source shapes through the project writer."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from plotroom.core.ask_checks import Ask
from plotroom.core.events import NewEvent
from plotroom.core.github import (
    COMMENTS,
    PULL_REQUESTS,
    ObservedPull,
    apply_comments,
    apply_pull_poll,
)
from plotroom.core.github_projection import PULL_TEXT, GitHubRecord, GitHubResource
from plotroom.core.human_queue import Decision
from plotroom.core.intake import SESSION_MESSAGE, TURN_REPORT
from plotroom.core.merge_flakes import FLAKE_EVENT
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.search import (
    ASK_KIND,
    DECISION_KIND,
    ISSUE_COMMENT_KIND,
    MESSAGE_KIND,
    PR_BODY_KIND,
    SearchHit,
    SearchPage,
    SearchRecord,
)
from test_search import message, opened_search

REPO = "fixture/project"
START = "2026-01-01T00:00:01Z"
LATER = "2026-01-01T00:00:02Z"
LATEST = "2026-01-01T00:00:03Z"
PULL_ID = f"{REPO}#7"
COMMENT_ID = f"{REPO}#2#issuecomment-42"


def comment(body: str, updated_at: str = START) -> GitHubRecord:
    return GitHubRecord(
        "42",
        "issue_comment",
        {"number": 2, "id": 42, "created_at": START, "updated_at": updated_at},
        body,
    )


async def project_comment(uow: UnitOfWork, row: GitHubRecord) -> None:
    cursor = await uow.fetch_cursor(REPO, COMMENTS)
    await apply_comments(uow, REPO, cursor, (row,), row.fields["updated_at"], LATEST)


async def project_pull(uow: UnitOfWork, pull: ObservedPull) -> None:
    cursor = await uow.fetch_cursor(REPO, PULL_REQUESTS)
    await apply_pull_poll(uow, REPO, cursor, (pull,), LATEST)


def indexed_rows(path: Path) -> dict[tuple[str, str], tuple[int, str]]:
    with closing(sqlite3.connect(path)) as db:
        return {
            (kind, record_id): (seq, body)
            for seq, kind, record_id, body in db.execute(
                "SELECT seq, kind, record_id, body FROM search_records"
            )
        }


async def test_current_sources_use_actual_ids_and_obey_role_kind_and_project_filters(
    tmp_path: Path,
) -> None:
    async with (
        opened_search(tmp_path, "demo") as (store, ledger),
        opened_search(tmp_path, "other") as (_, other),
    ):
        switchboard = (await ledger.append("system", message("one", "needle dispatch"))).event
        report = (
            await ledger.append(
                "system",
                NewEvent(
                    "claude",
                    TURN_REPORT,
                    "one",
                    {"session_id": "fixture", "report_digest": "fixture-digest"},
                    "needle report",
                ),
            )
        ).event
        session = (
            await ledger.append(
                "system",
                NewEvent(
                    "claude", SESSION_MESSAGE, "two", {"session_id": "fixture"}, "needle chat"
                ),
            )
        ).event
        flake = (
            await ledger.append(
                "system",
                NewEvent(
                    "core",
                    FLAKE_EVENT,
                    "one",
                    {
                        "repository": REPO,
                        "head_sha": "a" * 40,
                        "check_id": 8,
                        "check_name": "needle checks",
                        "pattern": "same head",
                    },
                ),
            )
        ).event
        decision = Decision("dec_fixture", "human", "  needle EXACT Words!\n", {}, [], [], START)
        ask = Ask("ask_fixture", REPO, 2, "needle ask", ("yes", "no"), 90, START, START)

        async def seed(uow: UnitOfWork) -> None:
            await uow.insert_decision(decision)
            await uow.save_ask(ask)
            await project_comment(uow, comment("needle comment"))
            await project_pull(
                uow, ObservedPull(7, "fixture", "a" * 40, REPO, "open", START, "needle pull")
            )

        await ledger.run("system", seed)
        foreign = (await other.append("system", message("one", "needle foreign"))).event
        public = {
            MESSAGE_KIND: switchboard.id,
            TURN_REPORT: report.id,
            SESSION_MESSAGE: session.id,
            FLAKE_EVENT: flake.id,
            ISSUE_COMMENT_KIND: COMMENT_ID,
            PR_BODY_KIND: PULL_ID,
        }
        expected = public | {DECISION_KIND: decision.decision_id, ASK_KIND: ask.ask_id}
        page = await ledger.search("needle", None, 10, role="orchestrator")
        assert page.total == len(expected)
        assert {hit.kind: hit.id for hit in page.hits} == expected
        (decision_hit,) = (hit for hit in page.hits if hit.kind == DECISION_KIND)
        assert "needle EXACT Words!" in decision_hit.excerpt
        assert (
            indexed_rows(store.path)[DECISION_KIND, decision.decision_id][1] == decision.exact_words
        )
        assert indexed_rows(store.path)[ASK_KIND, ask.ask_id][1] == ask.text

        for kind, record_id in expected.items():
            page = await ledger.search("needle", (kind, kind.upper()), 1, role="orchestrator")
            assert [(hit.kind, hit.id) for hit in page.hits] == [(kind, record_id)]
            assert page.total == 1
        page = await ledger.search("needle", None, 1, role="worker:fixture")
        assert page.total == len(public)
        assert page.hits[0].kind in public
        assert await ledger.search("needle", (DECISION_KIND, ASK_KIND), 10, role="clerk") == (
            SearchPage((), 0)
        )
        assert await ledger.search("needle", (), 10, role="orchestrator") == SearchPage((), 0)
        assert await other.search("needle", None, 10, role="orchestrator") == SearchPage(
            (SearchHit(foreign.id, MESSAGE_KIND, "needle foreign"),), 1
        )


async def test_source_edits_replay_empty_text_and_rollback(tmp_path: Path) -> None:
    async with opened_search(tmp_path, "demo") as (store, ledger):
        pull = ObservedPull(7, "fixture", "a" * 40, REPO, "open", START, "oldpull")

        async def seed(uow: UnitOfWork) -> None:
            await project_comment(uow, comment("oldcomment"))
            await project_pull(uow, pull)

        await ledger.run("system", seed)
        seeded = indexed_rows(store.path)

        async def edit(uow: UnitOfWork) -> None:
            await project_comment(uow, comment("newcomment", LATER))
            await project_pull(uow, replace(pull, updated_at=LATER, body="newpull"))

        async def failed_edit(uow: UnitOfWork) -> None:
            await edit(uow)
            assert await ledger.search("newpull", None, 10) == SearchPage((), 0)
            raise ValueError("rollback")

        with pytest.raises(ValueError, match="rollback"):
            await ledger.run("system", failed_edit)
        assert indexed_rows(store.path) == seeded

        await ledger.run("system", edit)
        current = indexed_rows(store.path)
        assert (
            current[ISSUE_COMMENT_KIND, COMMENT_ID][0] > seeded[ISSUE_COMMENT_KIND, COMMENT_ID][0]
        )
        assert current[PR_BODY_KIND, PULL_ID][0] > seeded[PR_BODY_KIND, PULL_ID][0]
        await ledger.run("system", edit)
        await ledger.run("system", seed)  # Earlier upstream versions arrive after the edit.
        assert indexed_rows(store.path) == current
        assert await ledger.search("oldcomment", None, 10) == SearchPage((), 0)
        assert await ledger.search("oldpull", None, 10) == SearchPage((), 0)
        assert await ledger.search("newcomment", None, 10) == SearchPage(
            (SearchHit(COMMENT_ID, ISSUE_COMMENT_KIND, "newcomment"),), 1
        )

        async def snapshots_and_removal(uow: UnitOfWork) -> None:
            projected = await uow.pull_request(REPO, 7)
            assert projected is not None
            await uow.save_pull_request(replace(projected, number=8))
            attrs = {"head_sha": projected.head_sha, "pull_version": projected.version}
            for number, updated, body in [
                (7, START, "stalebody"),
                (7, LATER, "sameversiondetail"),
                (8, LATER, "seededbydetail"),
            ]:
                await uow.save_github_resource(
                    REPO,
                    PULL_TEXT,
                    str(number),
                    GitHubResource(
                        (), attributes=attrs | {"pull_updated_at": updated, "body": body}
                    ),
                )
            await project_comment(uow, comment("", LATEST))
            await uow.index_search_record(
                SearchRecord(ISSUE_COMMENT_KIND, COMMENT_ID, "newcomment"), only_if_absent=True
            )

        await ledger.run("system", snapshots_and_removal)
        assert indexed_rows(store.path)[ISSUE_COMMENT_KIND, COMMENT_ID][1] == ""
        assert await ledger.search("newcomment", None, 10) == SearchPage((), 0)
        assert await ledger.search("stalebody", None, 10) == SearchPage((), 0)
        assert await ledger.search("sameversiondetail", None, 10) == SearchPage((), 0)
        assert await ledger.search("newpull", None, 10) == SearchPage(
            (SearchHit(PULL_ID, PR_BODY_KIND, "newpull"),), 1
        )
        assert await ledger.search("seededbydetail", None, 10) == SearchPage(
            (SearchHit(f"{REPO}#8", PR_BODY_KIND, "seededbydetail"),), 1
        )
