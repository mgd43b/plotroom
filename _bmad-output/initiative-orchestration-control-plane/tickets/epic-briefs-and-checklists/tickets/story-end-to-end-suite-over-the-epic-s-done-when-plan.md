---
title: 'Run Epic 6 acceptance through the local daemon'
type: feature
ticket: 6
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

Run Epic 6 acceptance through the local daemon as part of the local Epic 6 lane.

## Boundaries & Constraints

Follow the canonical story and Epic 6 Done-when criteria. Use local fixture evidence and the existing role, ledger and projection rules.

## Acceptance Criteria

Given the two Epic 6 Done-when lines, when the real local daemon receives their requests and fixture facts, then one deterministic test per line verifies the behavior through HTTP and stdio.

</frozen-after-approval>

## Code Map and Tasks

- [x] `tests/test_epic6_e2e.py` reuses local daemon, HTTP/stdio and GitHub read fixture patterns from `tests/test_epic3_e2e.py` and `tests/test_transport_contract.py`.
- [x] Assert the full brief and live other-lane claims in the first scenario. Assert READY activation, named refusal, final-step archive proposal and archive cleanup in the second. Use loopback or mock GitHub reads and deterministic synchronization.
- [x] Run the existing project acceptance and verify populated migration upgrade to the unique head after peer integration.

## Implementation Notes

The local daemon fixture reads a materialized ticket tree and observes pull facts through the existing HTTP mock. Both Done-when tests passed over HTTP and stdio. The integrated project suite covered 820 tests before review follow-up.

## Plan Change Log

## Review Triage Log

- Low, patch. Acceptance found that the transport fixture discarded the archive refusal message. The daemon test now asserts the names of all open steps on both transports.

## Verification

- `rtk proxy uv run --locked python -m pytest`
