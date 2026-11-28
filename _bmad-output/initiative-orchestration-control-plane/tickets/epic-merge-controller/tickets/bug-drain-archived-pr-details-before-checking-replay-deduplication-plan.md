---
title: 'Drain archived PR details before checking replay deduplication'
type: bugfix
ticket: 19
created: '2026-11-27'
status: built
baseline_revision: '2345d7ea91c15f3fcf1c33893f47c8dfc113f442'
route: full
route_source: pinned
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

The archived Epic 2 witness observes fixed terminal GitHub facts through the production poller. Let ordinary bounded terminal polling complete the fixed fixture before recording the event, action, and PR #2 detail-generation baseline. Verify that a normal replay of #2 advances its check and thread generations exactly once, with identical events and zero actions.

## Boundaries & Constraints

Keep production scheduling, broker scope and credentials, read/frame/time budgets, CLI, original full-mode criteria, and credential-backed integrated deferrals. Use only the already-approved public fixture and synthetic tests. Compose replays captured inputs with its network disabled. No protected PAT dispatch or tracker mutation. The implementation PR is held until #236 is verified and publication is routed.

## I/O & Edge-Case Matrix

| Scenario | Behavior |
| --- | --- |
| Healthy terminal queue contains #1, #2, #4, #5 | Production selector visits [1, 2], then [4, 5]. Take baseline only after all four current revision cursors are complete. |
| A healthy repeat follows stale cursor for #2 only | Production selector visits [2]. Both exact resources advance once, the event list is identical, and actions remain zero. |
| Missing or moved pin, incomplete warm-up, skipped pass, broker denial | Refuse within a fixed poll/time budget and clean up; never widen broker or synthesize reads. |
| Local capture and Compose replay | Equal observation result. No extra provider requests or egress. |

</frozen-after-approval>

## Code Map

- `deploy/epic_two_archived.py`, `project_witness` wraps the actual detail poll. Keep the first cursor and pinned assertions. Check fixed PR identity before accepting a current detail watermark. Exact extra closed heads #4 `68743618337ec4c7f562b3f5fb4f574c2a4a9f3d` and #5 `d58ac0958b8cb0411c08029dd1fd9218fc125b84` were read from the public fixture. #1 is merged and #2/#4/#5 closed. Each head repo is the fixture repo. Use names already in the module for existing heads and the existing terminal cursor shape. Record baseline only after warm-up; require one next PR poll and exact generations. Account for poll/read concurrency so a further pass cannot slip between observations. Preserve existing teardown and bounded wait on errors.
- `deploy/epic_two_archived.py`, `ProviderReads` and `compose_witness` provide bounded host reads and cache-only isolated replay. Leave those boundaries and the normal CLI entry unchanged.
- `src/plotroom/adapters/store_sqlite/github_sql.py` selects two terminal details per cycle, least recent attempts first. Test production scheduler sequence with deterministic fixtures.
- `tests/test_epic_two_acceptance.py` or a focused archive test file should cover completion, exhaustion, wrong pin, deduplicated replay, and cleanup as applicable.

## Tasks & Acceptance

- [x] `deploy/epic_two_archived.py` — Complete the bounded initial queue and preserve exact before/after observations.
- [x] `tests/` — Prove [1, 2], [4, 5], [2] and the refusal cases without provider mutations.

Given the four pinned PRs and healthy provider reads, when the normal witness runs locally and from capture in isolated Compose, then the same events and zero actions are reported and only the host makes public provider requests.

## Implementation Notes

- The witness checks exact archived PR identity and durable attempt watermarks after each parked pass. The events and action baseline follows the second pass.
- Actual public host and isolated Compose passed on the #234 tree. Both observed 36 events, zero actions, and PR cursor generation 3. The host made 38 provider reads, and the isolated replay served 63 cached responses with no additional provider calls.

## Plan Change Log

## Review Triage Log

- Medium, patch. A failed PR-list poll did not invoke the detail callback, so a later clean poll could hide it. The witness now reports a failed poll before a retry, and the new fixture proves refusal and teardown. Independent inspection confirmed the correction.

- Opening Kodus review found two gaps in the synthetic test. The fixture now isolates its GitHub environment and proves the witness rejects a valid event added during the replay pass.

## Verification

Built: [PR #238](https://github.com/mgd43b/plotroom/pull/238).

- Locked Python 3.14.10 and 3.15.2 each passed 97 focused tests covering the archive, production scheduler, and failure and teardown paths. Ruff lint and format, configured Pyright, and the diff check passed.
- The reviewed code on base `2345d7ea91c15f3fcf1c33893f47c8dfc113f442` completed the public host capture and actual network-disabled Compose replay. Each observed 36 events, zero actions, and PR-list generation 3 with terminal detail passes [1, 2], [4, 5], and [2]. The host made 38 provider reads. Compose replayed 63 responses with zero additional provider reads and zero owned resources left.
- Protected PAT verification remains deferred.
- After opening review, both locked interpreters passed 23 archive tests each with a synthetic inherited override.
