# Write-list: tree pass 2026-11-22, lane #14 findings on epic 1 (branch tree/epic-1-findings)

Status: HELD, waiting for the go-ahead. Nothing below has been done.

Summary: 2 new issues, 4 body edits, 2 parent links, 4 blocked-by relations, 2 label changes, no comments, nothing closed.

## Creates (in this order; milestone "Orchestration control plane"; no assignee)
| # | Entry | Type | Title | Parent | Blocked by | Labels |
|---|---|---|---|---|---|---|
| 1 | 1.17 | story | Bounded server log | #1 | #13 | story, backlog, risk:low |
| 2 | 1.18 | bug | Tests can lose their daemon's port between picking and binding it | #1 | #13 | bug, backlog, risk:medium |

## Body edits (body from the entry's or epic's file; each read first for tracker-side changes)
| Issue | Entry | What changed | Open PR touching the file? |
|---|---|---|---|
| #16 | 1.5 | Proxy readiness counts a 401; the real-client 401 check moves here (now hitl) | none |
| #24 | 1.15 | Lock refusal exits with code 4; the real-client 401 check moved to #16 (no longer hitl) | #49 edits it; this pass waits for #49 to merge |
| #26 | 1.12 | Scope gains the proxy readline block (deferred in PR #51); waits on the two new issues | none |
| #1 | epic-server-and-ledger | Notes: lock refusal exits with code 4 | #49 edits the epic folder; waits for #49 |

## Relations
- new 1.17 parent #1; new 1.18 parent #1
- new 1.17 blocked by #13; new 1.18 blocked by #13
- #26 blocked by new 1.17; #26 blocked by new 1.18

## Labels
- #16: add `hitl`. #24: remove `hitl`.

## Not on this list, on purpose
- The readline block is not its own ticket: lane #14 declined it in PR #51, so it is scope for the refactor sweep (#26).
