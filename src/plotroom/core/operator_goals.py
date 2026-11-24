"""Persist validated operator goals and report their current issue results."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from plotroom.core.events import LedgerError, format_time
from plotroom.core.github_projection import (
    ISSUE_PAGINATION_VERIFIED,
    ISSUE_RELATION_RETRY,
    ISSUE_TREE,
    GitHubResource,
)
from plotroom.core.goal_language import GoalSyntaxError, evaluate_goal, goal_predicates, parse_goal
from plotroom.core.goal_projection import GoalFacts, StoredGoal

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

FIRST_GOAL = (
    "every issue where state = open and milestone = 1 "
    "satisfies assigned_to human or blocked_by(assigned_to human)"
)


def validate_goal_name(name: str) -> None:
    try:
        length = len(name.encode("utf-8"))
    except UnicodeEncodeError:
        length = 0
    if not name.strip() or not 0 < length <= 128 or "\x00" in name:
        raise LedgerError("invalid", "goal name must be nonblank UTF-8 text up to 128 bytes")


async def set_goal(
    uow: UnitOfWork, args: dict[str, Any], *, repository: str | None
) -> dict[str, Any]:
    name: str = args["name"]
    text: str = args["text"]
    validate_goal_name(name)
    try:
        goal = parse_goal(text)
    except GoalSyntaxError as exc:
        raise LedgerError("invalid", str(exc)) from None
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise LedgerError("invalid", "goal text must be valid UTF-8") from None
    predicates = goal_predicates(goal)
    needs_blockers = "blocked_by" in predicates
    if repository is not None and needs_blockers and not await uow.goals_use_blockers():
        previous = await uow.github_resource(repository, ISSUE_TREE)
        tree = previous if previous is not None else GitHubResource(())
        retry = sorted(
            {row.fields["number"] for row in tree.records if row.kind == "issue_metadata"}
        )
        await uow.save_github_resource(
            repository,
            ISSUE_TREE,
            "",
            replace(
                tree,
                complete=False,
                generation=tree.generation + 1 if previous is not None else 0,
                attributes={
                    **tree.attributes,
                    ISSUE_RELATION_RETRY: retry,
                    ISSUE_PAGINATION_VERIFIED: False,
                },
            ),
        )
    await uow.save_goal(
        StoredGoal(
            name,
            text,
            format_time(uow.now()),
            needs_blockers,
            uses_lanes="lane" in predicates,
            uses_decisions="decision" in predicates,
        )
    )
    return {"name": name}


def report_goals(goals: list[StoredGoal], facts: GoalFacts) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for goal in goals:
        remaining = [failure.render() for failure in evaluate_goal(parse_goal(goal.text), facts)]
        reports.append(
            {
                "name": goal.name,
                "text": goal.text,
                "updated_at": goal.updated_at,
                "done": not remaining,
                "remaining": remaining,
            }
        )
    return {
        "done": all(report["done"] for report in reports),
        "goals": reports,
        "as_of": facts.as_of,
    }
