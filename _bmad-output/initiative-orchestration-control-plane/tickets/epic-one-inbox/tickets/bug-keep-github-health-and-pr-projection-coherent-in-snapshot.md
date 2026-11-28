---
id: 19
type: bug
title: "Keep GitHub health and PR projection coherent in snapshot()"
parent: epic-one-inbox
covers: [R1]
after: [2, "1.6"]
refined: true
hitl: false
risk: medium
tracker_id: "235"
remote: "https://github.com/mgd43b/plotroom/issues/235"
tracker_status: done
status: built
---

# Keep GitHub health and PR projection coherent in snapshot()

## Description

A snapshot can report GitHub available with a PR count and watermark from before the first successful poll. Availability and the accompanying board and GitHub summary should describe a coherent observation.

## Reproduction

In a disposable no-network diagnostic, hold a snapshot after the board read and commit the first successful poll with one open PR before rendering. The old response reports GitHub available, zero open PRs, and no watermark. The following snapshot sees one PR and a watermark. The mounted-PAT smoke asserted the count after observing availability and failed once in the original locked Python 3.14 verification. The parallel Python 3.15 run passed.

## Cause Hypothesis

The snapshot reads the committed board and GitHub summary, then reads the live health flag independently during rendering. A poll can advance health and the projection between those reads.

## Acceptance Criteria

1. Use deterministic synchronization at the normal MCP snapshot entry point to exercise the interleaving. The straddling response either consistently reports the pre-poll state, including unavailability, or the post-poll healthy state with the correct count and watermark. A subsequent response sees the committed result.
2. A successfully polled empty repository is available with zero open PRs and a committed watermark. Startup, provider failure, and recovery retain consistent health and existing public error behavior. A project without GitHub configuration keeps its existing response shape.
3. Preserve the existing mounted-PAT smoke assertions, snapshot roles, response bounds, and merge gate behavior. Run the deterministic regression and targeted snapshot and GitHub tests on both locked Python versions. Use synthetic local inputs, without test sleeps, assertion relaxation, or an opportunistic rerun as the fix.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox/epic-one-inbox.md, R1
- projection and mounted-PAT witness — https://github.com/mgd43b/plotroom/issues/29
- snapshot contract — https://github.com/mgd43b/plotroom/issues/17

## Notes

MCP captures GitHub health with the role-filtered committed board, PR count, and watermark.
The per-project guard covers observation capture, poll commit and health publication.
Provider requests stay outside the guard. A canceled poll waiting for the guard queues no
write, and a commit already in progress can finish publishing before it releases the guard.
Synthetic tests exercise startup, an empty successful poll, provider failure and recovery,
and a first poll forced between the MCP capture and render. The existing mounted-PAT
smoke uses its original assertions.
