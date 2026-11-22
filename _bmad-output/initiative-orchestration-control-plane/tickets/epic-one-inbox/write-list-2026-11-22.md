# Write-list: tree pass 2026-11-22, epic 2 (branch tree/epic-2-slice)

Status: HELD, waiting for mgd43b's go-ahead, typed in the ticketer's session. Nothing below has been done.

Summary: 18 new issues, 2 body edits, 18 parent links and 56 blocked-by relations, no label changes, no comments, nothing closed.

## Creates (in this order; milestone "Orchestration control plane")
| # | Entry | Type | Title | Parent (epic) | Blocked by | Labels | Assignee |
|---|---|---|---|---|---|---|---|
| 1 | 2.2 | story | Tracer: a merged PR on a test repository reaches the projection once | #2 | #15 (1.4), #16 (1.5), #17 (1.6) | story, backlog, risk:high, hitl | — |
| 2 | 2.7 | story | Switchboard adapter: receive and acknowledge as an MCP client | #2 | #15 (1.4), #16 (1.5), #18 (1.7), #19 (1.8) | story, backlog, risk:high | — |
| 3 | 2.1 | spike | Can plotroom read Claude desktop session state locally? | #2 | — | spike, backlog, risk:medium | — |
| 4 | 2.14 | story | PR updates, restarts and rate limits | #2 | new 2.2 | story, backlog, risk:medium | — |
| 5 | 2.3 | story | Checks, runs and image builds on main | #2 | new 2.14 | story, backlog, risk:medium, hitl | — |
| 6 | 2.4 | story | Review threads | #2 | new 2.3 | story, backlog, risk:medium | — |
| 7 | 2.15 | story | Labels, changed files and the lane's PR | #2 | new 2.4, new 2.7, #19 (1.8) | story, backlog, risk:medium | — |
| 8 | 2.5 | story | Issues and comments | #2 | new 2.15 | story, backlog, risk:low | — |
| 9 | 2.10 | story | Ticket tree from the default branch | #2 | new 2.2, #20 (1.13) | story, backlog, risk:low | — |
| 10 | 2.16 | story | Work items from GitHub Issues | #2 | new 2.5, new 2.10, #20 (1.13) | story, backlog, risk:medium | — |
| 11 | 2.6 | story | GitHub App credentials for hosted mode | #2 | new 2.2, #22 (1.10) | story, backlog, risk:medium, hitl | — |
| 12 | 2.11 | story | Deploy status from GitHub Deployments and lane reports | #2 | new 2.2, #16 (1.5), #19 (1.8) | story, backlog, risk:low | — |
| 13 | 2.17 | story | Switchboard adapter: send | #2 | new 2.7, #18 (1.7) | story, backlog, risk:medium | — |
| 14 | 2.8 | story | plotroom bridge for a hosted instance | #2 | new 2.7, new 2.17, #21 (1.9), #22 (1.10) | story, backlog, risk:medium | — |
| 15 | 2.9 | story | Claude intake through ingest(), the SessionReader port and inbound-message items | #2 | new 2.1, new 2.7, #15 (1.4), #16 (1.5), #17 (1.6), #19 (1.8), #21 (1.9) | story, backlog, risk:medium | — |
| 16 | 2.18 | story | Local SessionReader adapter | #2 | new 2.1, new 2.9 | story, backlog, risk:medium, hitl | — |
| 17 | 2.12 | story | Refactor sweep | #2 | new 2.2, new 2.7, new 2.1, new 2.14, new 2.3, new 2.4, new 2.15, new 2.5, new 2.16, new 2.10, new 2.6, new 2.11, new 2.17, new 2.8, new 2.9, new 2.18 | story, backlog, risk:low | — |
| 18 | 2.13 | story | End-to-end suite over the epic's Done when | #2 | new 2.12 | story, backlog, risk:low | — |

## Body edits (existing issues; body from the entry's file)
| Issue | Entry | What changed | Open PR touching the file? |
|---|---|---|---|
| #2 | epic-one-inbox | Refined: 11 requirements, constraints, 8 Done-when lines, decisions; whole-epic gate dropped | none |
| #1 | epic-server-and-ledger | One Notes line: Alembic revisions live in the package (coordinator finding from PR #28) | none (PR #28 does not touch the epic file) |

## Relations
- Each new issue: parent #2.
- new 2.2 blocked by #15
- new 2.2 blocked by #16
- new 2.2 blocked by #17
- new 2.7 blocked by #15
- new 2.7 blocked by #16
- new 2.7 blocked by #18
- new 2.7 blocked by #19
- new 2.14 blocked by new 2.2
- new 2.3 blocked by new 2.14
- new 2.4 blocked by new 2.3
- new 2.15 blocked by new 2.4
- new 2.15 blocked by new 2.7
- new 2.15 blocked by #19
- new 2.5 blocked by new 2.15
- new 2.10 blocked by new 2.2
- new 2.10 blocked by #20
- new 2.16 blocked by new 2.5
- new 2.16 blocked by new 2.10
- new 2.16 blocked by #20
- new 2.6 blocked by new 2.2
- new 2.6 blocked by #22
- new 2.11 blocked by new 2.2
- new 2.11 blocked by #16
- new 2.11 blocked by #19
- new 2.17 blocked by new 2.7
- new 2.17 blocked by #18
- new 2.8 blocked by new 2.7
- new 2.8 blocked by new 2.17
- new 2.8 blocked by #21
- new 2.8 blocked by #22
- new 2.9 blocked by new 2.1
- new 2.9 blocked by new 2.7
- new 2.9 blocked by #15
- new 2.9 blocked by #16
- new 2.9 blocked by #17
- new 2.9 blocked by #19
- new 2.9 blocked by #21
- new 2.18 blocked by new 2.1
- new 2.18 blocked by new 2.9
- new 2.12 blocked by new 2.2
- new 2.12 blocked by new 2.7
- new 2.12 blocked by new 2.1
- new 2.12 blocked by new 2.14
- new 2.12 blocked by new 2.3
- new 2.12 blocked by new 2.4
- new 2.12 blocked by new 2.15
- new 2.12 blocked by new 2.5
- new 2.12 blocked by new 2.16
- new 2.12 blocked by new 2.10
- new 2.12 blocked by new 2.6
- new 2.12 blocked by new 2.11
- new 2.12 blocked by new 2.17
- new 2.12 blocked by new 2.8
- new 2.12 blocked by new 2.9
- new 2.12 blocked by new 2.18
- new 2.13 blocked by new 2.12
- #2 stays blocked by #1 (the overview edge the coordinator's done check reads).

## Labels
- None created; every label used exists.

## Comments
- None.

## Closes
- None.

## Not on this list, on purpose
- Epic 1's tracer leaf (`story-tracer-...`), held for lane tracer-13's open branch.
- The spine's structural-seed change (Alembic path) is a repo change only; it reaches no issue.
- Whole-epic gates in epics 3 to 11's files: fixed when each epic is planned, not in this pass.

