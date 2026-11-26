---
title: Guardrails refactor sweep
type: refactor
ticket: 6
created: 2026-11-26
status: built
baseline_revision: bf96c117ef78dfbedb2ca050140b763d651bc95e
route: oneshot
route_source: pinned
risk: low
review: quick
review_source: pinned
lenses_ran: [quick]
---

<frozen-after-approval reason="human-owned intent">

## Intent

Account for Guardrails cleanup findings from the shipped story plans, review threads, and epic memlog. Apply justified local cleanup or identify the existing resolution and evidence. Route outstanding scope decisions to the ticketer.

</frozen-after-approval>

## Implementation Notes

Reviewed the five shipped Guardrails plans, the epic memlog, and the file comments on PRs #160 and #208. The checkout has no deferred-work file. These findings already have local resolution or an explicit boundary, so this sweep changes no production behavior.

| Finding | Disposition and evidence |
| --- | --- |
| Meter lag, incomplete samples, and timestamp integrity | The writer excludes incomplete samples before choosing the newest, and the OpenRouter writer retains its committed amount and as-of across lower or equal counters. The existing budget and OpenRouter suites verify lag, timeouts, and subsequent progress. |
| Observation and migration edge cases | Budget tests give a foreign meter a newer reading and verify a fixed-width pre-1000 timestamp. The populated guardrail upgrade test verifies preserved records and both budget scopes. |
| Run and key provenance | The OpenRouter plan and tests verify a pinned file, durable reservations including prior project databases, no reuse across local runs, pre-reservation cap validation, and secret exclusion. Malformed exponent, nesting, and duplicate-field tests reject untrusted reports. Unexpected nonempty project schemas fail closed. |
| Recoverable reads and abandoned polls | PR #208 separates recoverable target-read lock errors from corruption and cancellation, gives keys independent deadlines, and terminates archived-run polling after the final reconciliation window. Restart and deadline tests cover these cases. |
| Budget stop recipient and retry behavior | PR #160 fences lane ownership through relay submission, excludes stale native leases, retires stale outbox rows, and preserves the same key through interrupted relay receipts and expired native leases. The existing budget tests cover both channels and worker reassignment. A previously issued lease retains its receipt semantics, and relay retry after a lost reply retains its action key. |
| Ratio and routing | Existing ratio tests verify inclusive thresholds, restart and replay. The MCP contract exercises the typed leaf and route authorization. Route tests verify persisted assignments, empty-table seeding, and durable unrouted items. The prior routing review accepts authored assignments to future or archived lane IDs until changed by the orchestrator. |
| Bridged lane-pinned stop delivery | The hosted bridge plan and running guide explicitly use operator-visible failure for this case. Remote recipient fencing is a hosted-bridge design candidate and was handed to the ticketer as existing, out-of-scope work. The local stop checks remain covered. |
| Direct Anthropic Console | The epic memlog records the owner-selected OpenRouter-first scope. Console remains later work. |

Review evidence: [Guardrails local implementation](https://github.com/mgd43b/plotroom/pull/160), [incomplete observation handling](https://github.com/mgd43b/plotroom/pull/207), and [OpenRouter implementation](https://github.com/mgd43b/plotroom/pull/208).

## Plan Change Log

## Review Triage Log

- Independent review confirmed the existing-code disposition and asked that the hosted bridge handoff be recorded. This plan records the handoff and its existing design source.

## Verification

- Locked Python 3.14.10. All 64 tests passed in test_budgets.py, test_budget_ratio.py, test_routing.py, test_openrouter.py, and test_trigger_actions.py.
