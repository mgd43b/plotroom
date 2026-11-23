# Search re-filings and verified completions

Status: PUBLISHED 2026-11-23. Both re-filings, the #119 evidence links, and all eight completions were verified.

## Decision log

The [standing tracker delegation](../epic-server-and-ledger/write-list-2026-11-23-local-mcp-completions.md#decision-log) applies to the verified completions. The re-filing was separately approved in relay #811 on 2026-11-23: "Publish the exact two-item re-filing write list in #810."

## Re-filed findings

| Entry | GitHub issue | Parent | Labels |
|---|---|---|---|
| 10.8 | [#171 Permanent embedding rejection stalls vector indexing](https://github.com/mgd43b/plotroom/issues/171) | #10 | bug, backlog, risk:medium |
| 1.24 | [#172 Recover failed search indexing without restarting the daemon](https://github.com/mgd43b/plotroom/issues/172) | #1 | story, backlog, risk:medium |

Both issues are open and unassigned in the Orchestration control plane milestone. #119 links to both re-filings. The product decisions remain open in their respective tickets. GitHub's native Search rollup is 7 of 8 completed. #10 remains open with #171 as its open child. #1 remains open with #172 as a new child.

## Verified completions

| Issues | Merge evidence |
|---|---|
| #5 | All six native children completed and all five acceptance cases verified on PR #150's merge `3ecf20da`. Both Python jobs passed in [main CI](https://github.com/mgd43b/plotroom/actions/runs/45201121690). |
| #6 | All six native children completed and both acceptance cases verified on PR #159's merge `0b0e75fc`. Both Python jobs passed in [main CI](https://github.com/mgd43b/plotroom/actions/runs/45226874140). |
| #116–#120 | PR #168 merged at `0df87521`. The worker reported 125 focused checks per Python version, the populated 0018 to 0022 upgrade, and all three Search acceptance scenarios. Both Python jobs passed in [main CI](https://github.com/mgd43b/plotroom/actions/runs/45284972395). The two re-filing links provide #119's disposition evidence. |
| #169 | PR #170 merged at `1a3703d6`. The worker reported 74 focused checks per Python version. Both Python jobs passed in [main CI](https://github.com/mgd43b/plotroom/actions/runs/45281050591). The canonical plan links to PR #170. |

Removed only the backlog label from the eight completed issues. The parent mirrors use `status: done`, and the leaf mirrors use `tracker_status: done`.
