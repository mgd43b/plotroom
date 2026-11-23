# Verified local MCP and inbox completions

Status: PUBLISHED 2026-11-23 on "**Approve this closure write-list and tree PR**". All 12 completions verified.

## Decision log

- Decision: 2026-11-23, mgd43b in the Codex ticketer chat, on this pass: "**Approve this closure write-list and tree PR**".
- Decision: 2026-11-23, mgd43b in the Codex ticketer chat, on routine verified tracker completions and their tree PRs: "you don't need me to approve - this is your resonsibility".

## Published tracker writes

Removed the backlog label and marked each issue completed. Verified the 12 GitHub statuses and mirrored done in the corresponding BMad tickets.

| Entry | Issue | Verified merge |
|---|---|---|
| 1.21 | #138 | PR #143 |
| 1.23 | #140 | PR #143 |
| 1.6 | #17 | PR #144 |
| 1.7 | #18 | PR #144 |
| 1.8 | #19 | PR #144 |
| 1.13 | #20 | PR #144 |
| 1.9 | #21 | PR #144 |
| 1.15 | #24 | PR #144 |
| 2.7 | #30 | PR #145 |
| 2.17 | #41 | PR #145 |
| 2.9 | #43 | PR #145 |
| 2.14 | #32 | PR #145 |

## Verification

- PR #143 merged at 58f90f7b. The lane verified its implementation, tests, and both Built references. Both Python jobs passed in [main CI](https://github.com/mgd43b/plotroom/actions/runs/45125422292).
- PR #144 merged at fe1077b7. The lane verified the source and all six Built references. The 21 local post-merge checks covered HTTP and stdio lane operations across restart, repeated acknowledgment, watcher behavior, and both transport eras. Both Python jobs passed in [main CI](https://github.com/mgd43b/plotroom/actions/runs/45131827469).
- PR #145 merged at 518642b7. The lane and coordinator verified six Built references, 18 local inbox checks, and eight GitHub fixture checks. Coverage includes Switchboard receive and redelivery, stable retry keys on send, Claude ingest with watch and acknowledgment, and GitHub PR transitions and rate-limit recovery. Both Python jobs passed in [main CI](https://github.com/mgd43b/plotroom/actions/runs/45184631398).

## Remaining acceptance

- #29 retains its Built reference and needs the real test-repository check using a scoped PAT.
- #44 retains its Built reference and needs the real Claude Desktop check.
- #14 and #16 still need Matt's real-client observations.
