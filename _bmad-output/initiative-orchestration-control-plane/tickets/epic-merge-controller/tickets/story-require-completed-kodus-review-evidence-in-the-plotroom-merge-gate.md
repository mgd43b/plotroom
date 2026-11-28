---
id: 16
type: story
title: "Require completed Kodus review evidence in the Plotroom merge gate"
parent: epic-merge-controller
covers: [R1, R2, R5]
after: [1, 11, 14]
refined: false
hitl: false
risk: medium
tracker_id: "230"
remote: "https://github.com/mgd43b/plotroom/issues/230"
tracker_status: backlog
---

# Require completed Kodus review evidence in the Plotroom merge gate

## Description

Add a project-scoped requirement for completed review from the configured trusted Kodus App before Plotroom issues or executes a MERGE verdict. Use the same evidence rules wherever the product evaluates or revalidates a merge.

## Requirements

1. When the requirement is configured, accept a completed review only from the project’s trusted App and check identity, with review completion positively established for the repository, PR and exact evaluated head. Preserve the ordinary current-head checks and thread gates.
2. Accept a later-head cadence skip only if trusted evidence identifies the current skip and an independently established opening head of that same PR has completed review from the configured App. Require current-head threads to be complete, resolved and answered where the cadence policy requires answers. Unknown, contradictory, partial, stale or unreadable evidence leaves the merge refused. A generic skip, same-name check or successful status alone is insufficient.
3. Use one policy across advisory evaluation, live MERGE offers and execution-time revalidation, including fresh provider evidence and existing head and main pins. A head change or disappearing proof cannot carry an older MERGE decision forward. Keep the existing merge controller opt-in, unaffected project defaults, Rule 4 branch updates, other gate rules, and deployment and release deferrals.
4. Verify with bounded read-only provider fixtures. Cover valid completion, valid and invalid cadence, wrong App/repository/PR/head, missing or forged opening provenance, stale or incomplete evidence, pending review, unresolved or unanswered threads, unchanged CI requirements, and advisory/live/execution agreement. Existing gates and configuration behavior still pass when the option is absent.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md, R1, R2, R5
- routed finding — https://github.com/mgd43b/plotroom/pull/155
- completed review evidence contract — https://github.com/mgd43b/plotroom/issues/141 and https://github.com/mgd43b/plotroom/pull/158
- gate and thread prerequisites — https://github.com/mgd43b/plotroom/issues/63 and https://github.com/mgd43b/plotroom/issues/64

## Notes

The current product projection records a check's App ID and state and resolves threads on the current PR head. It does not yet establish the complete trusted review, opening-head, cadence and thread-answer evidence required here. The build must prove any added bounded evidence source. A first poll, an arbitrary prior SHA or a check name cannot establish the opening head. The bootstrap clerk's private state is not a product dependency.

The proposed scope is opt-in per project. The exact application identity and enablement may be selected for Plotroom once separately routed. Keep the merge-only policy from changing branch-update behavior. If maintaining existing Rule 4 ordering requires a different policy, route that choice before changing it.
