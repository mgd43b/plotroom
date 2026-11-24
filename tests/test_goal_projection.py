import json
from dataclasses import replace
from pathlib import Path

import pytest

from fakes import FixedClock
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError
from plotroom.core.github_projection import (
    ISSUE_PAGINATION_VERIFIED,
    ISSUE_TREE,
    ISSUES,
    GitHubRecord,
    GitHubResource,
)
from plotroom.core.goal_language import evaluate_goal, parse_goal
from plotroom.core.goal_projection import StoredGoal, issue_number, project_goal_facts
from plotroom.core.lanes import Lane
from plotroom.core.ledger import Ledger
from plotroom.core.operator_goals import FIRST_GOAL
from plotroom.core.snapshot import estimate_tokens, render_snapshot


def verified(records: tuple[GitHubRecord, ...]) -> GitHubResource:
    return GitHubResource(records, attributes={ISSUE_PAGINATION_VERIFIED: True})


def test_goal_facts_keep_project_scope_and_detect_stale_relationship_evidence() -> None:
    repo = "fixture/repo"
    issues = GitHubResource(
        (
            issue(1, "latest"),
            issue(2, "latest"),
            GitHubRecord("assignees/1", "issue_assignees", {"number": 1, "assignees": ["HUMAN"]}),
        )
    )
    tree = verified(
        (
            relation(1, "latest", [2, "foreign/repo#4"]),
            relation(2, "old", []),
        ),
    )
    facts = project_goal_facts(
        repo,
        "Human",
        issues,
        tree,
        [Lane("work", "alpha", ("Fixture/Repo#1", "foreign/repo#2"), "branch")],
        [("dec_one", ("#1", "7.2")), ("dec_two", ("2",))],
    )
    first, second = facts.issues.values()
    assert facts.human_login == "human"
    assert first.assignees == {"human"}
    assert first.blockers == (2, "foreign/repo#4")
    assert first.lanes == {"work"}
    assert first.decisions == {"dec_one"}
    assert second.blockers is None
    partial = GitHubResource(tree.records, complete=False)
    assert project_goal_facts(repo, "Human", issues, partial, [], []).issues[1].blockers is None
    assert second.decisions == {"dec_two"}
    assert not second.lanes


def test_non_issue_refs_do_not_resolve() -> None:
    for ref in ("7.2", "foreign/repo#7", "#0", "#-1", "1\n", "\uff19", "9999999999999999999"):
        assert issue_number("fixture/repo", ref) is None


def test_local_ticket_references_cannot_certify_a_numeric_issue_owner() -> None:
    repo = "fixture/repo"
    facts = project_goal_facts(
        repo,
        "Human",
        verified((issue(1, "latest"),)),
        None,
        [Lane("local", "alpha", ("1", "7.5"), "work")],
        [],
    )
    assert not facts.issues[1].lanes
    [failure] = evaluate_goal(
        parse_goal('every issue where state = open satisfies not lane "local"'), facts
    )
    assert failure.clauses[0].status == "unavailable"


async def test_goal_storage_reads_only_committed_projection_facts(tmp_path: Path) -> None:
    repo = "fixture/repo"
    store = SqliteStore("demo", tmp_path)
    try:
        await store.migrate()
        original = StoredGoal("first", "fixture original text", "before")
        updated = StoredGoal("first", "fixture revised text", "after")
        async with store.transaction() as tx:
            await tx.save_goal(original)
        assert await store.read_goals("first", repo, "human") == ([original], None)

        async with store.transaction() as tx:
            await tx.save_goal(updated)
            await tx.save_github_resource(repo, ISSUES, "", verified((issue(1, "after"),)))
            await tx.save_github_resource(
                repo, ISSUE_TREE, "", verified((relation(1, "after", []),))
            )
            assert await store.read_goals(None, repo, "human") == ([original], None)

        before = await store.head_seq()
        goals, facts = await store.read_goals(None, repo, "human")
        assert goals == [updated]
        assert facts is not None
        assert facts.issues[1].blockers == ()
        assert await store.head_seq() == before
        assert await store.read_goals("missing", repo, "human") == ([], None)
    finally:
        await store.close()


def issue(number: int, updated: str) -> GitHubRecord:
    return GitHubRecord(
        f"issue/{number}",
        "issue_opened",
        {"number": number, "state": "open"},
        details={
            "resource": "issue",
            "title": f"Issue {number}",
            "updated_at": updated,
            "milestone": 1,
            "labels": ["work"],
        },
    )


def relation(number: int, updated: str, blockers: list[int | str]) -> GitHubRecord:
    return GitHubRecord(
        f"metadata/{number}",
        "issue_metadata",
        {"number": number, "blocked_by": blockers},
        details={"updated_at": updated},
    )


async def test_operator_goal_preserves_source_and_reports_all_remaining_issues(
    tmp_path: Path,
) -> None:
    repo = "fixture/repo"
    name = "Milestone coverage"
    source = "  " + FIRST_GOAL.replace(" satisfies ", "\r\nsatisfies ") + "  "
    version = "2026-11-24T12:00:00Z"
    store = SqliteStore("demo", tmp_path)
    ledger = Ledger(
        store, build_catalog(), FixedClock(), github_repository=repo, human_login="Human"
    )
    try:
        await store.migrate()
        ledger.start()
        ledger.github_error = None
        assert await ledger.goal("orchestrator", None) == {"done": False, "goals": []}
        with pytest.raises(LedgerError, match="goal not found"):
            await ledger.goal("orchestrator", name)
        with pytest.raises(LedgerError) as rejected:
            await ledger.execute(
                "worker:example", "goal_set", "forbidden", {"name": name, "text": source}
            )
        assert rejected.value.code == "forbidden"
        stored = await ledger.execute(
            "orchestrator", "goal_set", "create", {"name": name, "text": source}
        )
        assert stored.result == {"name": name}
        with pytest.raises(LedgerError) as incomplete:
            await ledger.goal("orchestrator", name)
        assert incomplete.value.code == "unavailable"

        # A retained legacy projection remains stored but cannot certify the goal.
        async with store.transaction() as tx:
            await tx.save_github_resource(repo, ISSUES, "", GitHubResource((issue(1, version),)))
        with pytest.raises(LedgerError) as legacy:
            await ledger.goal("orchestrator", name)
        assert legacy.value.code == "unavailable"

        def assigned(number: int) -> GitHubRecord:
            return GitHubRecord(
                f"assignees/{number}", "issue_assignees", {"number": number, "assignees": ["HUMAN"]}
            )

        closed = replace(
            issue(4, version), kind="issue_closed", fields={"number": 4, "state": "closed"}
        )
        elsewhere = issue(6, version)
        elsewhere = replace(elsewhere, details={**elsewhere.details, "milestone": 2})
        base_issues = (
            issue(5, version),
            closed,
            issue(3, version),
            issue(2, version),
            issue(1, version),
            elsewhere,
            assigned(1),
            assigned(4),
        )
        tree = verified(
            (relation(2, version, [1]), relation(3, version, [4]), relation(5, version, []))
        )
        async with store.transaction() as tx:
            await tx.save_github_resource(repo, ISSUES, "", verified(base_issues))
            await tx.save_github_resource(repo, ISSUE_TREE, "", GitHubResource(tree.records))
        [unverified_tree] = (await ledger.goal("orchestrator", name))["goals"]
        assert unverified_tree["remaining"][0]["issue"] == 2
        assert unverified_tree["remaining"][0]["clauses"][1]["status"] == "unavailable"

        async with store.transaction() as tx:
            await tx.save_github_resource(repo, ISSUE_TREE, "", tree)
        cursor = await store.head_seq()
        result = await ledger.goal("orchestrator", name)
        assert result["done"] is False
        [report] = result["goals"]
        assert report["text"].encode() == source.encode()
        assert [item["issue"] for item in report["remaining"]] == [3, 5]
        assert [clause["clause"] for clause in report["remaining"][0]["clauses"]] == [
            "assigned_to human",
            "blocked_by(assigned_to human)",
        ]
        assert await ledger.goal("orchestrator", None) == result
        assert await store.head_seq() == cursor
        ledger.github_error = "fixture provider unavailable"
        with pytest.raises(LedgerError) as unavailable:
            await ledger.goal("orchestrator", name)
        assert unavailable.value.code == "unavailable"
        ledger.github_error = None

        with pytest.raises(LedgerError) as syntax:
            await ledger.execute(
                "orchestrator", "goal_set", "update", {"name": name, "text": source + "oops"}
            )
        assert syntax.value.code == "invalid"
        assert "line" in syntax.value.message
        assert await store.head_seq() == cursor
        assert await ledger.goal("orchestrator", name) == result

        async with store.transaction() as tx:
            await tx.save_github_resource(repo, ISSUE_TREE, "", replace(tree, complete=False))
        [uncertain] = (await ledger.goal("orchestrator", name))["goals"]
        assert [item["issue"] for item in uncertain["remaining"]] == [2, 3, 5]
        assert uncertain["remaining"][0]["clauses"][1]["status"] == "unavailable"

        async with store.transaction() as tx:
            await tx.save_github_resource(repo, ISSUE_TREE, "", tree)
            await tx.save_github_resource(
                repo, ISSUES, "", verified((*base_issues, assigned(3), assigned(5)))
            )
        assert (await ledger.goal("orchestrator", name))["done"] is True
        assert await store.head_seq() == cursor

        large = verified(tuple(issue(number, version) for number in range(1, 401)))
        async with store.transaction() as tx:
            await tx.save_github_resource(repo, ISSUES, "", large)
        [report] = (await ledger.goal("orchestrator", name))["goals"]
        assert len(report["remaining"]) == 400
        (seq, items, lane_counts, lanes), github = await ledger.snapshot_board("orchestrator")
        board = render_snapshot(
            "demo", seq, items, lane_counts, 256, lane_details=lanes, github=github
        )
        assert estimate_tokens(json.dumps(board)) <= 256
        assert await store.head_seq() == cursor
    finally:
        await ledger.stop()
        await store.close()

    reopened = SqliteStore("demo", tmp_path)
    try:
        [goal], _ = await reopened.read_goals(name, repo, "Human")
        assert goal.text.encode() == source.encode()
    finally:
        await reopened.close()
