# Tracker write-list for the #16 follow-ups

Status: PUBLISHED 2026-11-22 on "**Approve this write-list and tree PR**". Tracker writes verified.

Decision: 2026-11-22, mgd43b in the Codex ticketer chat, on the tracker pass: "**Approve this write-list and tree PR**". Approval covers the writes below and one tree PR.

The pass proposes four creates, four parent links, three new blockers for #26, two body edits, labels on the four new issues, and two completions. The backlog label comes off each completed issue.

## Creates

Create with title and body from the ticket file. Mirror all four new IDs and URLs in the tree before adding relations or labels.

| Order | Entry | Title | Parent | Labels |
|---|---|---|---|---|
| 1 | 1.21 ([#138](https://github.com/mgd43b/plotroom/issues/138)) | Bind local stdio credentials to the intended daemon | #1 | bug, backlog, risk:high |
| 2 | 1.22 ([#139](https://github.com/mgd43b/plotroom/issues/139)) | Honor configured CA trust for explicit HTTPS bearer connections | #1 | bug, backlog, risk:medium |
| 3 | 1.23 ([#140](https://github.com/mgd43b/plotroom/issues/140)) | Report and retry local cleanup after token revocation | #1 | bug, backlog, risk:medium |
| 4 | 4.14 ([#141](https://github.com/mgd43b/plotroom/issues/141)) | The clerk's gate merges without completed review evidence | #4 | bug, backlog, risk:medium |

Add all four to the existing Orchestration control plane milestone. The three Epic 1 bugs use the implementation in PR #134 as their baseline and can proceed while #16 awaits its real-client observations. Dispatch 1.21 first and sequence work on the shared stdio proxy with the coordinator. Entry 4.14 covers the copied bootstrap clerk gate.

## Relations

- Link each new issue to the parent in the table.
- Add the three new Epic 1 issues as blockers for #26.

## Body edits

- Update #1 from Epic 1's Notes to record the bound IP address, port, and Host behavior in the local daemon discovery path.
- Update #26 from entry 1.12 with five regression checks and its three new blockers. The checks cover port-file unpublication during HTTP shutdown, a digest mismatch for an existing token record, concurrent local-token updates for separate instances, receipt replay after a write rule is withdrawn, and HTTP snapshot refusal for the drain role.

## Verified completions

- Remove the backlog label from #129 and close it as completed. PR #135 merged at d45517c6. The coordinator verified teardown, and both Python jobs passed in [main CI](https://github.com/mgd43b/plotroom/actions/runs/45113032427).
- Remove the backlog label from #130 and close it as completed. PR #137 merged at 9a4db649. The worker passed 21 focused tests, and both Python jobs passed in [main CI](https://github.com/mgd43b/plotroom/actions/runs/45117133037).

#16 stays open for Matt's Claude Code and Codex observations of a bare 401. Route any Kodus prerequisite for Plotroom's merge controller through the coordinator for separate scope review.
