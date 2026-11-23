---
title: "Add the budget ratio trigger leaf"
type: feature
ticket: 4
created: 2026-11-23
status: built
baseline_revision: "c5cbfdca5cabcca23d22ad8e74e33a6b1a297de6"
route: full
route_source: pinned
risk: low
review: thorough
review_source: pinned
lenses_ran: ["blind-hunter", "edge-case-hunter", "verification-gap", "intent-alignment"]
review_loop_iteration: 0
context: ["{project-root}/AGENTS.md"]
---

<frozen-after-approval reason="human-owned intent">

## Intent

Trigger authors need to evaluate a run's budget spend at arbitrary ratios. The new typed level leaf compares the latest observed spend with the cap and uses the existing durable evaluator.

Refs #104.

## Boundaries & Constraints

- Compare the cumulative observation held in the budget projection.
- Treat a budget with no reading and an unknown budget as false. Compare finite nonnegative thresholds inclusively and support ratios above one.
- Keep the existing once and every lifecycle rules.

</frozen-after-approval>

## Code Map

- `core/triggers.py::build_leaf_registry` is the typed registry. Use a level leaf accepting `{"budget_ratio":{"run":"example","gte":0.9}}`.
- `core/budgets.py` shares exact threshold arithmetic with the built-in warnings. Use decimal-spelled rational comparison without dividing.
- `core/ledger.py` evaluates triggers after the tick's writes. `core/triggers.py::LeafRegistry.evaluate_open` persists the rising edge and fired state.

## Tasks & Acceptance

**Execution**

- [x] Register and validate the leaf in `core/triggers.py`.
- [x] Add repeat, stale-reading and restart coverage to `tests/test_budget_ratio.py`.

**Acceptance Criteria**

- Given a 90% trigger and fixture usage below the threshold, when a reading reaches 90% and subsequent readings remain above it, then the trigger fires once across replay and restart.
- Given a malformed leaf or an unobserved run, when it is created or evaluated, then malformed input is refused and the unobserved run remains false.

## Implementation Notes

- The registered level leaf uses exact decimal-spelled rational comparison. MCP and runtime checks cover inclusive thresholds, durable replay, and a fresh correction.

## Plan Change Log

## Review Triage Log

- Low · Plan status. Execution and verification are recorded when the combined build is accepted.
- Low · Patch. The transport contract now creates and inspects the typed ratio leaf through MCP alongside the runtime and replay cases.

## Verification

- `uv run python -m pytest tests/test_budget_ratio.py tests/test_trigger_predicates.py tests/test_triggers.py`
- Combined native review ran four independent lenses. Local verification passed on Python 3.14 and 3.15 with 828 tests each, plus focused migration, delivery, and transport regression checks. Ruff, Pyright, and the core dependency check passed.
- Built: https://github.com/mgd43b/plotroom/pull/160
