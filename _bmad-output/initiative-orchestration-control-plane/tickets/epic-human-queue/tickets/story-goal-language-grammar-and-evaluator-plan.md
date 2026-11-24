---
title: 'Parse and evaluate goals'
type: feature
ticket: '5'
created: '2026-11-23'
status: built
baseline_revision: '78471ab1fd992a3a1ea209ecdadff0eb5a622b06'
route: full
route_source: auto
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

An operator can express an invariant over typed issue projection and ledger facts and learn which predicates an issue fails.

## Boundaries & Constraints

Pure parser and evaluator, bounded syntax, positioned errors, deterministic ordering, conservative missing evidence.

| Input or state | Expected result |
| --- | --- |
| Malformed predicate | Error includes position |
| Two false alternatives | Report both failed clauses |
| One alternative is true | Issue satisfies rule |

</frozen-after-approval>

## Code Map

- core/goal_language.py — tokenize, parse immutable AST and evaluate fixture facts
- tests/test_goal_language.py — exercise grammar and reporting

## Tasks & Acceptance

- [x] core/goal_language.py — tokenize, parse immutable AST and evaluate fixture facts
- [x] tests/test_goal_language.py — exercise grammar and reporting
- [x] Verify the fixture cases and record the results here.

Given a typed fixture board, when the canonical rule runs, then every failing selected issue and its failed clauses are returned, with no I/O or mutation.

## Implementation Notes

- The owner approved the public grammar at https://github.com/mgd43b/plotroom/issues/83#issuecomment-7126510874. Use the recovered issue facts in `src/plotroom/core/goal_projection.py`.

```text
every issue where <predicate> satisfies <predicate>
every issue where state = open and milestone = 1 satisfies assigned_to human or blocked_by(assigned_to human)
```

- Predicate atoms are `state = open|closed`, `milestone = <positive integer>`, `label "…"`, `assigned_to human|"login"`, `blocked_by(<predicate>)`, `lane "…"` (active lane ID), and `decision "…"` (decision ID). Boolean operators in descending precedence are `not`, `and`, and `or`. Parentheses group. Quoted literals use JSON string escapes and case-sensitive label/lane/decision identity. GitHub logins compare case-insensitively.
- `blocked_by` matches an open issue on a direct dependency edge that satisfies the nested predicate. It does not silently traverse dependencies. Unavailable edges, foreign or absent blockers and missing human identity are unknown. Use three-valued boolean logic so `not` cannot convert unknown evidence to success. A selected issue passes only on true, and an unknown selection also prevents reporting done. Collect relevant failing leaves for conjunctions and alternative failures, preserving source order. Report issue numbers ascending with exact clause text and positions, distinguishing failed and unavailable evidence.
- Keep parsing and evaluation pure and bounded, with semantic errors carrying a source position. Implement and test `parse_goal(text: str) -> Goal`, `GoalSyntaxError` with a human-readable positioned message, and `evaluate_goal(goal: Goal, facts: GoalFacts) -> list[GoalFailure]`. `GoalFailure.render()` returns a JSON-compatible object for MCP. Leave storage, joining projection data and integration to #87.

## Plan Change Log

## Review Triage Log

- Independent inspection confirmed the approved grammar, positioned errors, direct open-blocker checks and deterministic issue and clause ordering. Unknown relationships and unresolved lane references remain unknown under negation.

## Verification

- `uv run --python 3.14 python -m pytest -q tests/test_goal_language.py tests/test_goal_projection.py` and the corresponding Python 3.15 command each passed 39 cases. Fixtures cover the canonical rule, exact positioned clauses, typed predicates and precedence, direct open blockers, unavailable evidence, bounds, and read-only facts.
- Ruff formatting and lint and strict Pyright passed for the parser, evaluator, and language fixtures.
- [GitHub story](https://github.com/mgd43b/plotroom/issues/83)
- Built: https://github.com/mgd43b/plotroom/pull/175
