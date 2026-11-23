---
title: "Composition and predicate validation"
type: feature
ticket: 11
created: '2026-11-23'
status: built
baseline_revision: '518642b7e89ca29eec8ce4f693f48f85eb966b02'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Validate and compose exact registered predicates.

**Approach:** Support structured all_of and any_of over registered leaves. Validate before creating a row and evaluate every leaf so glob bindings persist regardless of other leaf results.

## Boundaries & Constraints

Use the local synthetic inputs in this build, with the committed GitHub projection and existing writer and action contracts. Preserve project isolation and existing rows. Scope is the six assigned stories. Hosting, live client setup, and native drain delivery are outside this implementation.

## I/O & Edge-Case Matrix

| Scenario | Expected behavior |
| --- | --- |
| Unregistered leaf or arbitrary text | Refuse with an invalid reason before registration. |

</frozen-after-approval>

## Code Map

- `core/triggers.py` — Implement bounded recursive parsing and non-short-circuit persistent binding, then reduce the results.
- `tests/test_triggers.py` — Prove both merge orders, a restart between inputs, first true for any_of, and malformed or unknown leaf rejection.

## Tasks & Acceptance

- [x] `core/triggers.py` — Implement bounded recursive parsing and non-short-circuit persistent binding, then reduce the results.
- [x] `tests/test_triggers.py` — Prove both merge orders, a restart between inputs, first true for any_of, and malformed or unknown leaf rejection.

Given a synthetic project daemon, when the relevant input is submitted through the writer or MCP surface, then the behavior in the edge-case matrix is observable in the durable project state.

## Implementation Notes

- Validated registered leaf names and declared argument types before saving. Evaluated nested groups and persisted every glob binding.

## Plan Change Log

## Review Triage Log

- Independent native review and follow-up. Converted overdeep input into an invalid refusal and covered it. SQLite restart cases cover both merge orders and any_of behavior.

## Verification

The local trigger, recovery, action, transport, and migration suite passed all 50 tests on Python 3.14. Ruff, Pyright, and the core dependency check passed. Independent native review confirmed the corrected acceptance paths.

Run `uv run python -m pytest -q` and check that the targeted local acceptance tests pass, including the existing transport contract.
