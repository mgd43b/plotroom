---
title: Epic 8 Guardrails acceptance suite
type: feature
ticket: 7
created: 2026-11-26
status: built
baseline_revision: b665731a6ca87168161fa450c1d0253fa7e72705
route: oneshot
route_source: pinned
risk: low
review: quick
review_source: pinned
lenses_ran: [quick]
---

<frozen-after-approval reason="human-owned intent">

## Intent

Cover each of Epic 8's two Done-when lines through the real local daemon. Count 50%, 80%, and 100% effects once, use both local delivery fixtures, and verify hands-on route lookup and the unrouted next item through MCP.

</frozen-after-approval>

## Implementation Notes

- tests/test_epic8_e2e.py drives real writer ticks with a fixed clock and fixture usage meter. It reuses the scripted Switchboard child, native drain stand-in, and existing MCP tools harness. The normal action runner remains live.
- Fixtures use loopback and temporary state. No real provider calls or remote workers.

## Plan Change Log

## Review Triage Log

- Independent review confirmed both Done-when paths and the fixture boundaries. Added a pre-cap action check and verified newer spend and timestamp after restart so replay assertions cannot pass by dropping the sample.

- Opening review added checks immediately below 50%, 80%, and 100%, and cancellation-safe test daemon startup. The helper regression blocks both cleanup tasks until the test verifies startup is still pending.

## Verification

- The neighboring suite passed 74 tests on each locked Python version. The two final acceptance tests passed on both versions after the review additions. Ruff, Pyright, and the dependency check passed.

- After opening review, 96 affected tests passed on each locked Python version. The final two Epic 8 tests and the startup cleanup regression also passed on both versions.
