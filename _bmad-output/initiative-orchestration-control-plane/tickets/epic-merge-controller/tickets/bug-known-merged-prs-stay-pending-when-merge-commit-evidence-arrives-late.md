---
id: 15
type: bug
title: "Known merged PRs stay pending when merge commit evidence arrives late"
parent: epic-merge-controller
covers: [R5]
after: [5, "2.14"]
refined: false
hitl: false
risk: medium
tracker_id: "218"
remote: "https://github.com/mgd43b/plotroom/issues/218"
tracker_status: backlog
---

# Known merged PRs stay pending when merge commit evidence arrives late

## Description

A known merged PR can remain pending in the merge log when the provider's list and detail responses omit its merge commit SHA and the PR falls behind incremental polling. When sufficient provider evidence becomes available later, the controller should record the observed merge exactly once.

## Reproduction

In a disposable shadow run, a known merged [PR #210](https://github.com/mgd43b/plotroom/pull/210) had no merge SHA in either its list or detail response. After the row fell behind the incremental watermark, it remained pending. Supplying late provider evidence to the existing reconciler produced no duplicate PR events or actions. The shadow recorded one pending gate observation.

## Cause Hypothesis

The incremental scan may stop revisiting an already-merged row before its merge identity is known.

## Acceptance Criteria

1. **Recover after the watermark.** Given an already-known merged PR behind the incremental watermark, with its merge SHA missing from ordinary reads, when later provider evidence establishes the merge identity for the configured repository, exact PR, known head, and applicable merge method, the normal reconciler records the verified SHA and completes the pending observation once under its existing key and attribution rules.
2. **Keep incomplete evidence pending.** Missing, capped, truncated, ambiguous, or mismatched evidence leaves the observation pending. A later sufficient result can still reconcile it. Preserve any established merge identity. Do not guess a SHA or substitute the PR head or branch tip for a proven merge identity.
3. **Prove bounded, read-only recovery.** Disposable provider fixtures verify an explicit cap on reconciliation reads, zero provider writes, and no watermark regression. Replaying the same evidence or restarting produces no duplicate PR events, gate observations, or actions. Cover wrong repository, PR, head, and merge-method semantics.
4. **Record proof if current code already meets the criteria.** If the reproduction and the bounded failure and recovery checks already pass, record the proof here.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md, R5
- prerequisite — https://github.com/mgd43b/plotroom/issues/71, observed merge attribution
- prerequisite — https://github.com/mgd43b/plotroom/issues/32, incremental PR polling and restart
- witness — https://github.com/mgd43b/plotroom/pull/210
- provider semantics — https://docs.github.com/en/rest/using-the-rest-api/issue-event-types#merged
- provider semantics — https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request

## Notes

For the single public witness, the timeline value matched the independently confirmed merge SHA. GitHub documents the event field in terms of the merged HEAD and the PR merge SHA in terms of the merge method. This leaves the evidence source and supported methods to be established by tests. This work uses disposable fixtures.
