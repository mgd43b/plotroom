---
title: "MERGE items, observed merges and refusals the lane sees"
type: feature
ticket: '5'
created: '2026-11-23'
status: built
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

Present merge and refusal items to their owners and classify authoritative merge observations.

## Boundaries & Constraints

Preserve guard order, the deploy freeze, exact head and main evidence, and durable action safety. Use injected local fixtures for write effects. Keep the clerk's production gate outside the change.

</frozen-after-approval>

## Code Map

- `src/plotroom/core/merge_controller.py` — Route readiness and marked fixes, retire stale pins, deduplicate refusals, classify merges before clearing prior readiness.
- `src/plotroom/core/github.py` — Project draft state and the observed merge-commit SHA.
- `src/plotroom/adapters/mcp/server.py` — Reuse next and wait. Provide the advertised head and main to the dry check and the full pinned clerk command in item context.

## Tasks & Acceptance

- [x] `src/plotroom/core/merge_controller.py` — Route readiness and marked fixes, retire stale pins, deduplicate refusals, classify merges before clearing prior readiness.
- [x] `src/plotroom/core/github.py` — Project draft state and the observed merge-commit SHA.
- [x] `src/plotroom/adapters/mcp/server.py` — Reuse next and wait. Provide the advertised head and main to the dry check and the full pinned clerk command in item context.

- Given a matching open readiness item, when an observed merge commits, then it logs gated with the real merge SHA. Lane refusals appear once per head and reason.

## Implementation Notes

- Items share the project writer and its role-filtered next and wait views. A pinned operator recheck reads fresh provider facts and revalidates local policy before returning MERGE. Default offers go to the available clerk or orchestrator, with marked fixes reserved for the orchestrator.

## Plan Change Log

## Review Triage Log

- A cached-only pinned query could approve a same-head unresolved thread. The pinned query now confirms authoritative current details. The daemon fixture exercises the thread change before projection refresh.
- A new lane binding could lose a deduplicated refusal. The item now follows the owner, including after an intervening pass, with one delivery per recipient and one report.
- An observation missing its merge SHA could lose gated classification. A pending observation preserves the initial classification until the matching commit arrives. Legacy merged rows can gain a SHA without a new incident.
- Missing draft evidence could pass or erase a previously observed draft. Unknown draft state now refuses, and upgraded pull rows retain unknown until verified.
- Clerk role changes and disabling the configured workflow could leave stale routing. Role changes are applied on reconciliation, and startup cleans disabled readiness items.

- An unread skip label could let intermediate poll facts create refusals. Live processing now waits for the skip decision.

- The authoritative read now records provider backoff on rate limits and refuses overflowing provider timestamps.

- Opening review found a final-read race on main. The fresh recheck now confirms the default branch and tip after the other provider reads. The item identifies the head-only enforcement of the printed GitHub command. The injected fixture verifies both pins at dispatch.

## Verification

- `uv run python -m pytest -q tests` — acceptance and regression suite passes
- Built: https://github.com/mgd43b/plotroom/pull/155
