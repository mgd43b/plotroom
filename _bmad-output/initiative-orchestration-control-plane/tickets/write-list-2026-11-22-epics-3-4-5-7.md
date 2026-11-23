# Write-list: tree pass 2026-11-22, epics 3, 4, 5 and 7 (branch tree/epics-3-4-5-7; its PR opens from main after #47 merges)

Status: HELD, waiting for mgd43b's go-ahead, typed in the ticketer's session. Nothing below has been done.

Summary: 38 new issues, 16 body edits, 38 parent links, 110 blocked-by relations on new issues and 3 on existing epics, 1 label removal, 1 comment and 1 close (#13).

## Creates (in this order; milestone "Orchestration control plane"; no assignee)
| # | Entry | Type | Title | Parent | Blocked by | Labels |
|---|---|---|---|---|---|---|
| 1 | 3.1 | story | Tracer: a pr_merged trigger notifies the orchestrator once | #3 | #17, #18, #29 | story, backlog, risk:high |
| 2 | 3.5 | story | Exactly once across restarts, and branch-glob binding | #3 | new 3.1 | story, backlog, risk:high |
| 3 | 3.8 | story | Listing and cancelling triggers | #3 | new 3.5 | story, backlog, risk:low |
| 4 | 3.11 | story | Composition and predicate validation | #3 | new 3.8 | story, backlog, risk:medium |
| 5 | 3.2 | story | PR-state leaves | #3 | new 3.11, #33, #34, #36 | story, backlog, risk:low |
| 6 | 3.3 | story | Run reports, run_finished and main_green_with_images | #3 | new 3.2, #19, #33 | story, backlog, risk:medium |
| 7 | 3.4 | story | Event leaves, deploys, idle lanes and every-triggers | #3 | new 3.3, #30, #43, #40 | story, backlog, risk:medium |
| 8 | 3.6 | story | Actions: send, mark and propose_archive | #3 | new 3.1, #19, #41 | story, backlog, risk:medium |
| 9 | 3.7 | story | Claude-lane outbox and its drain | #3 | new 3.6, #16, #21 | story, backlog, risk:high, hitl |
| 10 | 3.9 | story | Refactor sweep | #3 | new 3.1, new 3.5, new 3.8, new 3.11, new 3.2, new 3.3, new 3.4, new 3.6, new 3.7 | story, backlog, risk:low |
| 11 | 3.10 | story | End-to-end suite over the epic's Done when | #3 | new 3.9 | story, backlog, risk:low |
| 12 | 4.1 | story | Tracer: merge_gate(pr) judges checks like the clerk's gate | #4 | #33, #32 | story, backlog, risk:high |
| 13 | 4.11 | story | Rule 2: unresolved review threads | #4 | new 4.1, #34 | story, backlog, risk:medium |
| 14 | 4.6 | story | Flake candidates on PRs and on main | #4 | #33, #36, #35 | story, backlog, risk:medium |
| 15 | 4.2 | story | Rule 3: main's verdict and the marked fix | #4 | new 4.1, new 4.11, new 4.6, #35 | story, backlog, risk:medium |
| 16 | 4.3 | story | Rule 4: stale CI and the automatic branch update | #4 | new 4.2, #35, #18 | story, backlog, risk:high, hitl |
| 17 | 4.12 | story | The deploy window | #4 | new 4.2 | story, backlog, risk:low |
| 18 | 4.4 | story | Rule 5: closing keywords | #4 | new 4.1, #36 | story, backlog, risk:low |
| 19 | 4.13 | story | Rule 6, live evaluation and the merge log | #4 | new 4.3, new 4.4, new 4.11, new 4.12 | story, backlog, risk:medium |
| 20 | 4.5 | story | MERGE items, observed merges and refusals the lane sees | #4 | new 4.13, new 4.2, new 4.3, #19, #21 | story, backlog, risk:medium |
| 21 | 4.7 | story | One open PR per lane | #4 | new 4.13, #35, #19 | story, backlog, risk:low |
| 22 | 4.8 | story | Controller-run merges, built off | #4 | new 4.5, new 4.3 | story, backlog, risk:medium |
| 23 | 4.9 | story | Refactor sweep | #4 | new 4.1, new 4.11, new 4.6, new 4.2, new 4.3, new 4.12, new 4.4, new 4.13, new 4.5, new 4.7, new 4.8 | story, backlog, risk:low |
| 24 | 4.10 | story | End-to-end suite over the epic's Done when | #4 | new 4.9 | story, backlog, risk:low |
| 25 | 5.1 | story | Tracer: a conflicting claim raises an overlap item | #5 | #19 | story, backlog, risk:medium |
| 26 | 5.2 | story | PR diffs against other lanes' claims | #5 | new 5.1, #35 | story, backlog, risk:medium |
| 27 | 5.3 | story | Ticket-tree moves against open PRs | #5 | new 5.2, #37 | story, backlog, risk:low |
| 28 | 5.4 | story | overlaps() and claims in snapshot() | #5 | new 5.2, new 5.3 | story, backlog, risk:low |
| 29 | 5.5 | story | Refactor sweep | #5 | new 5.1, new 5.2, new 5.3, new 5.4 | story, backlog, risk:low |
| 30 | 5.6 | story | End-to-end suite over the epic's Done when | #5 | new 5.5 | story, backlog, risk:low |
| 31 | 7.1 | story | Tracer: decide() records the human's exact words | #7 | #16, #36 | story, backlog, risk:medium |
| 32 | 7.5 | story | Goal language: grammar and evaluator | #7 | new 7.1, #19, #36 | story, backlog, risk:high |
| 33 | 7.2 | story | Recording asks | #7 | #16, #36 | story, backlog, risk:low |
| 34 | 7.3 | story | Ask checks: buried, optionless, stale and orphaned | #7 | new 7.1, new 7.2, #18 | story, backlog, risk:medium |
| 35 | 7.4 | story | Decisions settle asks and tickets | #7 | new 7.1, new 7.3 | story, backlog, risk:low |
| 36 | 7.6 | story | Storing goals, goal() and the first goal | #7 | new 7.5, new 7.1, #38 | story, backlog, risk:medium |
| 37 | 7.7 | story | Refactor sweep | #7 | new 7.1, new 7.5, new 7.2, new 7.3, new 7.4, new 7.6 | story, backlog, risk:low |
| 38 | 7.8 | story | End-to-end suite over the epic's Done when | #7 | new 7.7 | story, backlog, risk:low |

## Body edits (existing issues; body from the entry's or epic's file; each read first for tracker-side changes)
| Issue | Entry | What changed | Open PR touching the file? |
|---|---|---|---|
| #3 | epic-triggers | Refined: requirements, constraints, decisions; whole-epic gate dropped | this pass |
| #4 | epic-merge-controller | Refined: requirements, constraints, decisions (clerk's gate as reference); gate dropped | this pass |
| #5 | epic-claims-and-overlaps | Refined: requirements, two Done-when lines, decisions; gate dropped | this pass |
| #7 | epic-human-queue | Refined: requirements, a Done-when line, decisions; gate dropped | this pass |
| #2 | epic-one-inbox | Review fixes: one work-item source per project (R8), a lane-inference Done-when line | this pass |
| #31 | 2.1 | The spike unblocks or drops entry 18 | this pass |
| #32 | 2.14 | Closes and reopens; per-PR fetcher completeness and as_of for the gate | this pass |
| #33 | 2.3 | Job conclusions per run; latest run per check name | this pass |
| #34 | 2.4 | Full pagination; an incomplete thread list is marked | this pass |
| #35 | 2.15 | Rename status and previous path; hands on to epics 4 and 5 | this pass |
| #36 | 2.5 | Comment authors and creation times; hands on to epic 7 | this pass |
| #37 | 2.10 | One work-item source per project | this pass |
| #38 | 2.16 | One work-item source per project | this pass |
| #29 | 2.2 | How a daemon started on demand reads its PAT (secret file), from lane #14 | none after #47 merges |
| #42 | 2.8 | Note on real clients' protocol eras and cancellation, from lane #14 | none after #47 merges |
| #24 | 1.15 | Note on real clients' protocol eras and cancellation, from lane #14 | none |

## Relations
- Each new issue: parent is its epic's issue (#3, #4, #5 or #7).
- #4 blocked by #1, #5 blocked by #1, #7 blocked by #1 (the epic-1 declarations added in the initiative breakdown).
- new 3.1 blocked by #17
- new 3.1 blocked by #18
- new 3.1 blocked by #29
- new 3.5 blocked by new 3.1
- new 3.8 blocked by new 3.5
- new 3.11 blocked by new 3.8
- new 3.2 blocked by new 3.11
- new 3.2 blocked by #33
- new 3.2 blocked by #34
- new 3.2 blocked by #36
- new 3.3 blocked by new 3.2
- new 3.3 blocked by #19
- new 3.3 blocked by #33
- new 3.4 blocked by new 3.3
- new 3.4 blocked by #30
- new 3.4 blocked by #43
- new 3.4 blocked by #40
- new 3.6 blocked by new 3.1
- new 3.6 blocked by #19
- new 3.6 blocked by #41
- new 3.7 blocked by new 3.6
- new 3.7 blocked by #16
- new 3.7 blocked by #21
- new 3.9 blocked by new 3.1
- new 3.9 blocked by new 3.5
- new 3.9 blocked by new 3.8
- new 3.9 blocked by new 3.11
- new 3.9 blocked by new 3.2
- new 3.9 blocked by new 3.3
- new 3.9 blocked by new 3.4
- new 3.9 blocked by new 3.6
- new 3.9 blocked by new 3.7
- new 3.10 blocked by new 3.9
- new 4.1 blocked by #33
- new 4.1 blocked by #32
- new 4.11 blocked by new 4.1
- new 4.11 blocked by #34
- new 4.6 blocked by #33
- new 4.6 blocked by #36
- new 4.6 blocked by #35
- new 4.2 blocked by new 4.1
- new 4.2 blocked by new 4.11
- new 4.2 blocked by new 4.6
- new 4.2 blocked by #35
- new 4.3 blocked by new 4.2
- new 4.3 blocked by #35
- new 4.3 blocked by #18
- new 4.12 blocked by new 4.2
- new 4.4 blocked by new 4.1
- new 4.4 blocked by #36
- new 4.13 blocked by new 4.3
- new 4.13 blocked by new 4.4
- new 4.13 blocked by new 4.11
- new 4.13 blocked by new 4.12
- new 4.5 blocked by new 4.13
- new 4.5 blocked by new 4.2
- new 4.5 blocked by new 4.3
- new 4.5 blocked by #19
- new 4.5 blocked by #21
- new 4.7 blocked by new 4.13
- new 4.7 blocked by #35
- new 4.7 blocked by #19
- new 4.8 blocked by new 4.5
- new 4.8 blocked by new 4.3
- new 4.9 blocked by new 4.1
- new 4.9 blocked by new 4.11
- new 4.9 blocked by new 4.6
- new 4.9 blocked by new 4.2
- new 4.9 blocked by new 4.3
- new 4.9 blocked by new 4.12
- new 4.9 blocked by new 4.4
- new 4.9 blocked by new 4.13
- new 4.9 blocked by new 4.5
- new 4.9 blocked by new 4.7
- new 4.9 blocked by new 4.8
- new 4.10 blocked by new 4.9
- new 5.1 blocked by #19
- new 5.2 blocked by new 5.1
- new 5.2 blocked by #35
- new 5.3 blocked by new 5.2
- new 5.3 blocked by #37
- new 5.4 blocked by new 5.2
- new 5.4 blocked by new 5.3
- new 5.5 blocked by new 5.1
- new 5.5 blocked by new 5.2
- new 5.5 blocked by new 5.3
- new 5.5 blocked by new 5.4
- new 5.6 blocked by new 5.5
- new 7.1 blocked by #16
- new 7.1 blocked by #36
- new 7.5 blocked by new 7.1
- new 7.5 blocked by #19
- new 7.5 blocked by #36
- new 7.2 blocked by #16
- new 7.2 blocked by #36
- new 7.3 blocked by new 7.1
- new 7.3 blocked by new 7.2
- new 7.3 blocked by #18
- new 7.4 blocked by new 7.1
- new 7.4 blocked by new 7.3
- new 7.6 blocked by new 7.5
- new 7.6 blocked by new 7.1
- new 7.6 blocked by #38
- new 7.7 blocked by new 7.1
- new 7.7 blocked by new 7.5
- new 7.7 blocked by new 7.2
- new 7.7 blocked by new 7.3
- new 7.7 blocked by new 7.4
- new 7.7 blocked by new 7.6
- new 7.8 blocked by new 7.7

## Labels
- None created; every label used exists.
- #13: remove `backlog` (done is the closed state).

## Comments (exact text)
**On #13:**
> Done: built in PR #28, merged at ec2d985e; main's ci.yml run on that commit passed (https://github.com/mgd43b/plotroom/actions/runs/45087667919).

## Closes (verification recorded)
- #13 (entry 1.2, the tracer) as completed: PR #28 merged at ec2d985e, and main's ci.yml run 45087667919 on that commit succeeded (checked 2026-11-22); verification by the coordinator.

## Not on this list, on purpose
- Epic 1's tracer story file: only its tracker status is mirrored to done after the close.
- Whole-epic gates in epics 6 and 8 to 11: dropped when each is planned.

