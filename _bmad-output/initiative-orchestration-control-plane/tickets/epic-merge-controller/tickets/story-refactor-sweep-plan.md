---
title: 'Refactor sweep'
type: refactor
ticket: '9'
created: '2026-11-23'
status: in-review
baseline_revision: 'b9bb69382b42edec2a4112113e278dade63a3dd8'
route: full
route_source: auto
risk: low
review: thorough
review_source: pinned
lenses_ran: [native-safety, native-lifecycle, native-acceptance]
review_loop_iteration: 0
context: ['AGENTS.md']
---

<frozen-after-approval reason="human-owned intent">

## Intent

Account for deferred Epic 4 findings and keep cleanup scoped to controller code.

## Boundaries & Constraints

Preserve guard order, the deploy freeze, exact head and main evidence, and durable action safety. Use injected local fixtures for write effects. Keep the clerk's production gate outside the change.

</frozen-after-approval>

## Code Map

- `src/plotroom/core/merge_gate.py` — Reuse evidence bindings and a single evaluator.
- `src/plotroom/core/merge_controller.py` — Reuse typed item and event lifecycle conventions.
- `src/plotroom/core/branch_updates.py` — Keep existing branch update guard ordering.

## Tasks & Acceptance

- [x] `src/plotroom/core/merge_gate.py` — Reuse evidence bindings and a single evaluator.
- [x] `src/plotroom/core/merge_controller.py` — Reuse typed item and event lifecycle conventions.
- [x] `src/plotroom/core/branch_updates.py` — Keep existing branch update guard ordering.

- Given previous Epic 4 plans and reviews, when swept, then findings are accounted for or routed to the ticketer.

## Implementation Notes

- The prior review corrections for check producers and bounded action scans landed in c3d9ab0. This change shares pull-evidence binding and retains one gate evaluator. The ticketer received legacy Commit Status contexts, product-side completed Kody evidence, and the existing detail-poll batch and freshness mismatch as follow-ups.

## Plan Change Log

## Review Triage Log

- The existing batch cap can expire details periodically on a large project queue. The ticketer received the scheduling follow-up.

- Shell parity uses the C-locale word boundary. The existing clerk script inherits the invoking shell locale, so a UTF-8 locale can accept certain non-ASCII word prefixes that Plotroom refuses.

## Verification

- `uv run python -m pytest -q tests` — acceptance and regression suite passes
