---
title: 'Controller run merges built off'
type: feature
ticket: '8'
created: '2026-11-23'
status: in-review
baseline_revision: 'b9bb69382b42edec2a4112113e278dade63a3dd8'
route: full
route_source: auto
risk: high
review: thorough
review_source: pinned
lenses_ran: [native-safety, native-lifecycle, native-acceptance]
review_loop_iteration: 0
context: ['AGENTS.md']
---

<frozen-after-approval reason="human-owned intent">

## Intent

Allow explicitly enabled fixture merges with fresh authoritative evidence and exact head and main pins.

## Boundaries & Constraints

Preserve guard order, the deploy freeze, exact head and main evidence, and durable action safety. Use injected local fixtures for write effects. Keep the clerk's production gate outside the change.

</frozen-after-approval>

## Code Map

- `src/plotroom/app/config.py` — Parse strict Boolean controller_merges and a validated merge method, with opt-in off by default.
- `src/plotroom/app/serve.py` — Allow test-only adapter injection for a loopback fixture project.
- `src/plotroom/app/runtime.py` — Recheck current local evidence in the writer before fixture execution.
- `src/plotroom/ports/github_write.py` — Require fresh authoritative gate inputs and atomic compare-and-apply fixture writes with stable idempotency.

## Tasks & Acceptance

- [x] `src/plotroom/app/config.py` — Parse strict Boolean controller_merges and a validated merge method, with opt-in off by default.
- [x] `src/plotroom/app/serve.py` — Allow test-only adapter injection for a loopback fixture project.
- [x] `src/plotroom/app/runtime.py` — Recheck current local evidence in the writer before fixture execution.
- [x] `src/plotroom/ports/github_write.py` — Require fresh authoritative gate inputs and atomic compare-and-apply fixture writes with stable idempotency.

- Given opt-in is off or the fixture adapter is absent, when a MERGE verdict appears, then no controller write is possible. A fully pinned opt-in writes once in fixtures.

## Implementation Notes

- The per-project Boolean opt-in and injected loopback write port are independent prerequisites. Dispatch rechecks the local and authoritative fixture verdicts and passes both SHA pins plus the evidence revision into the fixture compare-and-apply.

## Plan Change Log

## Review Triage Log

- Changing main, merge method or readiness could strand the old controller action or suppress a later one. The queue now distinguishes readiness occurrences and retires obsolete ones. The fixture effect key stays stable across occurrences at the same refs.

## Verification

- `uv run python -m pytest -q tests` — acceptance and regression suite passes
