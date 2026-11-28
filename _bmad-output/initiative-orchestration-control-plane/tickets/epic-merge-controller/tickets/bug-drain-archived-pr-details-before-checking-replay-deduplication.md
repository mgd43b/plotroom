---
id: 19
type: bug
title: "Drain archived PR details before checking replay deduplication"
parent: epic-merge-controller
covers: [R1]
after: [18, "2.2"]
refined: false
hitl: false
risk: medium
tracker_id: "237"
remote: "https://github.com/mgd43b/plotroom/issues/237"
tracker_status: backlog
---

# Drain archived PR details before checking replay deduplication

## Description

The archived Epic 2 witness assumes PR #2 is refreshed on its next poll. The bounded terminal scheduler can still have unattempted archived PRs, so the witness fails its generation check before verifying replay deduplication.

## Reproduction

On the reviewed #234 merge, the archived witness read pinned provider data and failed its PR #2 detail-generation assertion. The offline diagnostic observed the normal terminal scheduler select PRs #1 and #2, then unattempted PRs #4 and #5, then PR #2 after its disposable cursor was invalidated. The witness took its baseline before the terminal backlog drained.

## Cause Hypothesis

The witness assumes its target is the next terminal row selected. The scheduler prioritizes unattempted rows and permits two terminal rows per pass.

## Acceptance Criteria

1. In the archived witness, wait for the known initial terminal backlog to drain through normal production polling within a fixed bound. Fail clearly if it does not drain. Take the replay baseline after that initial enrichment.
2. Invalidate only the disposable PR #2 detail cursor. Verify that the actual next target refresh advances the check and thread resource generations once. Assert exact equality of the event ledger and zero actions across that replay.
3. Prove the same result on the host and in isolated Compose replay using saved or synthetic provider inputs. Preserve the production scheduler, the bounded read broker and its endpoint permissions, and the existing checks for the full #46 suite. No extra status reads, credential changes, external writes, or hosted acceptance claims.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md, R1
- scheduler — https://github.com/mgd43b/plotroom/issues/233 and https://github.com/mgd43b/plotroom/pull/234
- fixture facts — https://github.com/mgd43b/plotroom/issues/29
- affected suite and prior witness — https://github.com/mgd43b/plotroom/issues/46 and https://github.com/mgd43b/plotroom/pull/225
