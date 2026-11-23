---
title: 'Clean up verified Epic 6 implementation findings'
type: refactor
ticket: 5
created: '2026-11-23'
status: built
baseline_revision: '65ed0835eeab89276f197f375cc1ebdcd87dfc54'
route: full
route_source: auto
review: thorough
review_source: pinned
lenses_ran: [acceptance, lifecycle, integration]
risk: low
context: [AGENTS.md]
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Clean up verified Epic 6 implementation findings as part of the local Epic 6 lane.

## Boundaries & Constraints

Follow the canonical story and Epic 6 Done-when criteria. Use local fixture evidence and the existing role, ledger and projection rules.

## Acceptance Criteria

Given the four Epic 6 implementation plans and their findings, when they are audited, then only confirmed Epic 6 cleanup is applied and other findings go to the ticketer.

</frozen-after-approval>

## Code Map and Tasks

- [x] Read the four Epic 6 plans and the feature diff for open findings. Add only repairs supported by a test or source evidence.
- [x] Inspect `src/plotroom/core/checklists.py` and the brief-rendering core for duplicate feature wiring or loose boundaries. Keep the user-facing behavior and unrelated areas intact.
- [x] Record the disposition of each deferred finding here and alert the ticketer to confirmed issues outside Epic 6.

## Implementation Notes

The feature uses one archive-proposal helper for completion and trigger actions. Fixture initialization uses the configured ticket reader and an explicit first mirror commit so acceptance does not depend on wall-clock tick cadence. The pre-review sweep found no issues to route outside Epic 6.

## Plan Change Log

## Review Triage Log

- The combined review found eight distinct Epic 6 issues across thirteen reports. All were addressed within the feature and its verification.
- The targeted recheck found two residual cases in the brief read and READY summary. The feature applies both corrections, with focused regression coverage.

## Verification

- `rtk proxy uv run --locked ruff check src tests` and the Epic 6 focused suite
- [Built in PR #159](https://github.com/mgd43b/plotroom/pull/159)
