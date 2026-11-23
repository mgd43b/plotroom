---
title: 'Bound trigger arguments before the command copy'
type: bugfix
ticket: 12
created: '2026-11-23'
status: built
baseline_revision: '190b09d8ead462f60698343285594cc1a5243f72'
route: oneshot
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

**Problem:** The existing oversized trigger regression reaches Python's recursive command snapshot before the trigger registry checks predicate depth. On Python 3.14, the failing copy can emit unraisable recursion warnings despite the caller receiving an invalid LedgerError.

**Approach:** Bound traversal of trigger command containers before the first deep copy, below the interpreter's recursion limit and above the existing accepted predicate depth. Refuse excessive input with the current invalid error. Keep the normal command snapshot and preserve its replay and isolation rules, the trigger grammar, and the existing depth regression.

</frozen-after-approval>

## Implementation Notes

The regular ledger still owns recursive copying and command receipts. The trigger module supplies a bounded pre-copy check, and its registry continues to apply the 16-level predicate rule.

## Plan Change Log

## Review Triage Log

- Replay used to reject a changed over-depth body before the writer loaded the receipt. The snapshot now carries a sentinel and the writer decides after receipt lookup. A matching receipt replays its authorized result. A new request returns the existing invalid error.
- The initial scan revisited shared predicate objects once per path. It now remembers the deepest visit by identity. Regression coverage reaches a single subtree on both ordinary and excessive-depth paths.

## Verification

- Managed Python 3.14.10 and 3.15.2 each passed 74 focused tests and all 973 tests.
- Ruff, formatting, Pyright for both Python versions, and the core dependency check passed.
- The bmad-build reviewer and native Codex review confirmed the replay fix and shared-container handling after follow-up.

- Run the targeted trigger, command, and transport tests on managed Python 3.14 and 3.15.
- Run the full pytest suite on both interpreters and the normal lint, format, typing, and dependency checks.
- Independently review the exact diff before opening the PR.
- [Built in PR #170](https://github.com/mgd43b/plotroom/pull/170)
