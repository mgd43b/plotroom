---
id: 13
type: story
title: "End-to-end suite over the epic's Done when"
parent: epic-one-inbox
covers: [R1, R2, R3, R4, R5, R6, R7, R8, R9, R10, R11]
after: [12, "1.10", 2, 6]
hitl: false
risk: low
tracker_id: "46"
remote: "https://github.com/mgd43b/plotroom/issues/46"
tracker_status: done
---

# End-to-end suite over the epic's Done when

## Description

Provide one Epic 2 acceptance runner for the local daemon, isolated Compose with a scripted switchboard participant, and the available integrated CI witnesses from the dedicated test repository. Report each epic Done-when row with its evidence and any deferred live requirement.

## Requirements

Verify: The scoped local daemon and isolated Compose checks pass. The reviewed integrated CI run verifies archived GitHub terminal facts and lane inference and the read-only published work-item mirrors. Report each Done-when row, cite the spike answer in the epic Notes, and leave hosted bridge delivery, hosted GitHub App refresh, and a recorded GitHub deployment plus owned lane report deferred under Epic #2.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md

## Notes

- Decision (2026-11-28, Plotroom coordinator chat): Matt said “don’t leave #46 blocked on work we’ve deferred. close it on the passing local and Compose evidence, keep the live checks on #2, and leave the live pilot, deployment, and publishing deferred.”
- The [protected integrated run](https://github.com/mgd43b/plotroom/actions/runs/46114285478) passed on exact main `0a60033e3e2807904b943e6c10d81f0699310d19` after [PR #238](https://github.com/mgd43b/plotroom/pull/238). It verified the local and isolated Compose witnesses, archived terminal GitHub facts and lane inference, and matching read-only work-item sources. Host and Compose each recorded 36 events, zero actions, and pull cursor generation 3. The host made 38 archive reads. Compose replayed captured responses.
- [PR #236](https://github.com/mgd43b/plotroom/pull/236) separately passed 254 focused tests per locked Python on exact main `cb8dc6a06001b2d0d0ac9acf06216e8890d7cdc2`, including the mounted-PAT smoke, snapshot coherence, and archived replay. [Exact main CI](https://github.com/mgd43b/plotroom/actions/runs/46115319948)
- [#123](https://github.com/mgd43b/plotroom/issues/123) supplies a real local kind bridge round trip and authenticated client snapshots. [#39](https://github.com/mgd43b/plotroom/issues/39) supplies the separately accepted local live App refresh with clean credential scans. These are local evidence. The integrated run leaves hosted bridge delivery, hosted App refresh, and the recorded deployment and owned lane report deferred under Epic #2. The full Epic 2 runner remains nonzero while those live witnesses are absent.
