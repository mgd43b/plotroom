import json
from collections.abc import Iterable
from dataclasses import replace
from types import MappingProxyType

import pytest

from plotroom.core.goal_language import GoalSyntaxError, evaluate_goal, parse_goal
from plotroom.core.goal_projection import GoalFacts, GoalIssue

FIRST_GOAL = (
    "every issue where state = open and milestone = 1 "
    "satisfies assigned_to human or blocked_by(assigned_to human)"
)


def issue(number: int, **changes: object) -> GoalIssue:
    return replace(
        GoalIssue(
            number=number,
            title=f"Issue {number}",
            state="open",
            milestone=1,
            labels=frozenset(),
            assignees=frozenset(),
            blockers=(),
            lanes=frozenset(),
            decisions=frozenset(),
        ),
        **changes,
    )


def facts(issues: Iterable[GoalIssue], human: str | None = "Operator") -> GoalFacts:
    return GoalFacts(
        "fixture/repo", MappingProxyType({entry.number: entry for entry in issues}), human
    )


def test_canonical_goal_reports_both_alternatives_for_each_selected_violation() -> None:
    board = facts(
        [
            issue(9, blockers=(2,)),
            issue(8, state="closed"),
            issue(7, milestone=2),
            issue(6, blockers=(4,)),
            issue(5, blockers=(3,)),
            issue(4, state="closed", assignees=frozenset({"operator"})),
            issue(3, milestone=2, assignees=frozenset({"OPERATOR"})),
            issue(2, blockers=(3,)),
            issue(1, blockers=("FIXTURE/REPO#3",)),
        ]
    )
    goal = parse_goal(FIRST_GOAL)

    failures = evaluate_goal(goal, board)

    assert [failure.issue for failure in failures] == [6, 9]
    rendered = [failure.render() for failure in failures]
    assert json.loads(json.dumps(rendered)) == rendered
    for number, failure in zip((6, 9), rendered, strict=True):
        assert failure == {
            "issue": number,
            "title": f"Issue {number}",
            "clauses": [
                {
                    "clause": clause,
                    "position": FIRST_GOAL.index(clause),
                    "status": "failed",
                }
                for clause in ("assigned_to human", "blocked_by(assigned_to human)")
            ],
        }
    assert evaluate_goal(goal, board) == failures


@pytest.mark.parametrize(
    ("rule", "matches"),
    [
        ('label "one" or label "two" and label "three"', True),
        ('(label "one" or label "two") and label "three"', False),
        ('not label "two" and label "one"', True),
        ('not (label "two" or label "one")', False),
        ('not not label "one"', True),
        ('label "One"', False),
        (r'label "one\u0022two\\three"', True),
        ('assigned_to "OpErAtOr"', True),
        ('assigned_to "human"', False),
        ('lane "Work" and decision "Approved"', True),
        ('lane "work" or decision "approved"', False),
        ("state = closed or milestone = 1", True),
    ],
)
def test_typed_predicates_and_boolean_precedence(rule: str, matches: bool) -> None:
    goal = parse_goal(f"every issue where state = open satisfies {rule}")
    board = facts(
        [
            issue(
                1,
                labels=frozenset({"one", 'one"two\\three'}),
                assignees=frozenset({"OPERATOR"}),
                lanes=frozenset({"Work"}),
                decisions=frozenset({"Approved"}),
            )
        ]
    )

    assert bool(evaluate_goal(goal, board)) is not matches


def test_failed_evidence_preserves_source_order_and_spelling() -> None:
    source = (
        'every issue where state=open satisfies (label  "x" and not  (milestone=1)) or lane "z"'
    )
    (failure,) = evaluate_goal(parse_goal(source), facts([issue(1)]))
    clauses = ('label  "x"', "not  (milestone=1)", 'lane "z"')
    assert [(clause.text, clause.position, clause.status) for clause in failure.clauses] == [
        (clause, source.index(clause), "failed") for clause in clauses
    ]


@pytest.mark.parametrize(
    ("blockers", "human"),
    [
        (None, "operator"),
        ((2,), "operator"),
        (("other/repo#1",), "operator"),
        ((1,), None),
    ],
)
def test_unavailable_blocker_or_human_evidence_survives_negation(
    blockers: tuple[int | str, ...] | None, human: str | None
) -> None:
    clause = "not blocked_by(assigned_to human)"
    goal = parse_goal(f"every issue where state=open satisfies {clause}")
    (failure,) = evaluate_goal(goal, facts([issue(1, blockers=blockers)], human))

    assert [(evidence.text, evidence.status) for evidence in failure.clauses] == [
        (clause, "unavailable")
    ]


def test_one_known_open_blocker_can_prove_an_existential_predicate() -> None:
    goal = parse_goal(FIRST_GOAL)
    board = facts(
        [
            issue(1, blockers=("other/repo#2", 2, 3, 4)),
            issue(4, state="closed"),
            issue(3, assignees=frozenset({"operator"})),
        ]
    )

    assert evaluate_goal(goal, board) == []


def test_explicit_nested_blockers_only_follow_each_direct_edge() -> None:
    source = "every issue where milestone=1 satisfies blocked_by(blocked_by(assigned_to human))"
    board = facts(
        [
            issue(1, blockers=(2,)),
            issue(2, milestone=2, blockers=(3,)),
            issue(3, milestone=2, assignees=frozenset({"operator"})),
        ]
    )

    assert evaluate_goal(parse_goal(source), board) == []


def test_unknown_selection_prevents_done_but_false_selection_does_not() -> None:
    source = "every issue where milestone=1 and assigned_to human satisfies state=open"
    (failure,) = evaluate_goal(
        parse_goal(source), facts([issue(1), issue(2, milestone=2)], human=None)
    )

    assert failure.issue == 1
    assert [(item.text, item.status) for item in failure.clauses] == [
        ("assigned_to human", "unavailable")
    ]


def test_three_valued_or_keeps_both_relevant_alternatives() -> None:
    source = 'every issue where state=open satisfies label "needs-human" or assigned_to human'
    (failure,) = evaluate_goal(parse_goal(source), facts([issue(1)], human=None))

    assert [(item.text, item.status) for item in failure.clauses] == [
        ('label "needs-human"', "failed"),
        ("assigned_to human", "unavailable"),
    ]


@pytest.mark.parametrize(
    ("predicate", "position"),
    [
        ("state = pending", 8),
        ("milestone = 0", 12),
        ('milestone = "1"', 12),
        ("milestone = -1", 12),
        ("assigned_to operator", 12),
        ("lane Work", 5),
        ('__import__("os")', 0),
        ("blocked_by(state=open", 21),
        (r'label "bad\q"', 10),
        ("state=open\nor", 13),
        ("state=open and satisfies", 15),
    ],
)
def test_invalid_predicates_produce_positioned_errors(predicate: str, position: int) -> None:
    prefix = "every issue where state=open satisfies\n"
    with pytest.raises(GoalSyntaxError, match=r"line [23], column [1-9][0-9]*") as error:
        parse_goal(prefix + predicate)

    assert error.value.position == len(prefix) + position


@pytest.mark.parametrize(
    ("predicate", "message"),
    [
        ("not " * 40 + "state=open", "nesting is too deep"),
        (" or ".join(["state=open"] * 140), "too many tokens"),
        ('label "' + "x" * 4096 + '"', "text is too long"),
    ],
)
def test_parser_enforces_size_and_nesting_limits(predicate: str, message: str) -> None:
    with pytest.raises(GoalSyntaxError, match=message):
        parse_goal(f"every issue where state=open satisfies {predicate}")
