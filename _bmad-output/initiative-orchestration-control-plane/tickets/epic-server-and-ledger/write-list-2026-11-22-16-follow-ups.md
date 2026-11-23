# Write-list: tree pass 2026-11-22, #16 follow-ups, #129 done, the clerk-gate finding (branch tree/epic-1-16)

Status: DRAFT, HELD. mgd43b gave the publish go-ahead to the Claude coordinator, which has stood down. That go-ahead waits until mgd43b says, in the ticketer's session, who gives it now. Nothing below has been done on GitHub.

Summary: 4 new issues, 2 body edits, 4 parent links, 6 blocked-by relations, labels on 4 new issues and 1 removed, no comments, 1 close.

On mgd43b's list: nothing to do here. One open question, flagged under the 4.14 create: it would add a new requirement to epic 4 if taken up.

## Creates (in this order; milestone "Orchestration control plane"; no assignee)
| # | Entry | Type | Title | Parent | Blocked by | Labels |
|---|---|---|---|---|---|---|
| 1 | 1.21 | bug | The stdio proxy can send its bearer to a listener it has not authenticated | #1 | #16 | bug, backlog, risk:high |
| 2 | 1.22 | bug | An https bearer connection ignores configured CA trust | #1 | #16 | bug, backlog, risk:medium |
| 3 | 1.23 | bug | Token revoke can report a failure after it has revoked | #1 | #16 | bug, backlog, risk:medium |
| 4 | 4.14 | bug | The clerk's gate merges without completed review evidence | #4 | — | bug, backlog, risk:medium |

- 1.21 groups four of the review's seven findings. 1.22 and 1.23 take one each. That leaves one finding unplaced until the review text comes back: it goes to #26 if it is only a test-strength gap.
- 4.14 is bootstrap maintenance on the copied clerk skill. Its open question: plotroom's own gate gets no matching rule unless epic 4 gains a requirement for one.

## Body edits (body from the entry's or epic's file; each read first for tracker-side changes)
| Issue | Entry | What changed |
|---|---|---|
| #1 | epic 1 | The port-file Decision line records the published bound host and the bearer rule since #16 (PR #134) |
| #26 | 1.12 | Scope gains the port-file early-unpublication test (Kodus review of PR #135); after gains 21, 22, 23 |

## Relations
- Each new issue: parent is its epic's issue (#1 or #4).
- new 1.21 blocked by #16
- new 1.22 blocked by #16
- new 1.23 blocked by #16
- #26 blocked by new 1.21, new 1.22 and new 1.23

## Labels, comments, closes
- #129: remove `backlog`; close as completed. PR #135 merged at d45517c6, main CI is green, and the coordinator verified it. The tree already says done.
- No comments.

## Not on this list, on purpose
- #16 stays open, assigned to mgd43b, until their two real-client 401 observations are in epic 1's Notes.
- No P0–P3 labels: no issue uses them yet. Dispatch order is the coordinator's.
