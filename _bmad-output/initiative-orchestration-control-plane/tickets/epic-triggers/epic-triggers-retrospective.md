---
epic: epic-triggers
date: 2026-11-25
verdict: accepted-with-open-items
criteria: declared
headless: true
---

# Epic 3 Triggers retrospective

## Epic summary

Evidence baseline is main at `75b30834c29e1be3c618af173d228b55c94e0ca5`. The epic file, ticket index, all twelve canonical stories and plans, implementation merges, completion records, and CI runs were available. BMad reports all twelve plans built and all twelve tracker states done. GitHub reports all twelve native children completed. There are no unfinished or built-only tickets. The epic issue was open when checked. No session logs or separate epic run folders were available for this review. Process analysis uses the plans and merged review records.

| BMad entries, in build order | Tracker issues, all done | Plan baseline and attributable implementation |
|---|---|---|
| 3.1, 3.5, 3.8, 3.11 | #52, #53, #54, #55 | `518642b7`. Engine foundation, restart and binding, lifecycle, and composition. [PR #148](https://github.com/mgd43b/plotroom/pull/148) |
| 3.2, 3.3, 3.4 | #56, #57, #58 | `3ecf20da`. GitHub leaves, run reporting, and recurring event and idle leaves. [PR #154](https://github.com/mgd43b/plotroom/pull/154) |
| 3.6, 3.7 | #59, #60 | `518642b7`. Keyed dispatch and native drain contract. The real drain witness was recorded later. [PR #148](https://github.com/mgd43b/plotroom/pull/148), [PR #184](https://github.com/mgd43b/plotroom/pull/184) |
| 3.9, 3.10 | #61, #62 | `3ecf20da`. Refactor sweep and the four end-to-end acceptance cases. [PR #154](https://github.com/mgd43b/plotroom/pull/154) |
| 3.12 | #169 | `190b09d8`. Predicate-depth handling and replay coverage. [PR #170](https://github.com/mgd43b/plotroom/pull/170) |

The plan-baseline ranges are `518642b7..3ecf20da`, `3ecf20da..190b09d8`, and `190b09d8..1a3703d6`. The last range ends at the #170 implementation merge because later main commits also contain unrelated work. Plans sharing a baseline have no distinct ticket-by-ticket ranges. Their shared implementation merge provides attribution. The first two windows contain unrelated commits. The aggregate and code review therefore use the three first-parent implementation deltas at `331a79b3`, `65ed0835`, and `1a3703d6`, plus the #60 evidence-only delta at `4f15cb9`. The git evidence pass measured five of seven merges in the first baseline window, 17 of 31 in the second, and one of one in the third. The first-parent deltas separately capture what landed in the relevant merges.

## Findings

**Architecture and behavior.** The landed code keeps leaf validation and transition evaluation in the core registry, persists trigger state with the writer transaction, and leaves external delivery to the action runner and adapters. The dependency check passed at the baseline. The foundation introduced 229 lines in the trigger module. Coverage then added 285 and removed 44. The final file has 550 lines, with distinct registry, evaluation, and validation responsibilities. This was the largest new production module in the trigger-specific deltas. The sweep converged on one PR selection path and one transition implementation. The bounded review found no concrete duplicated transition path or layering regression. Disposition is accept as-is. [Trigger implementation](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/src/plotroom/core/triggers.py), [sweep evidence](tickets/story-refactor-sweep-plan.md)

**Spec reconciliation.** The canonical suite says “the test repository.” The frozen #62 plan specifies an isolated in-process GitHub fixture read through the real adapters. The fixture establishes the four daemon behaviors. The live repository and image workflow checks remain tracked by #29 and #33. The #60 real-drain witness establishes native delivery separately. Disposition is accept the fixture evidence for Epic 3 and retain both live checks with their owning stories. [Suite plan](tickets/story-end-to-end-suite-over-the-epic-s-done-when-plan.md), [#29](https://github.com/mgd43b/plotroom/issues/29), [#33](https://github.com/mgd43b/plotroom/issues/33)

**Adversarial review and R4 wording.** The epic says “latest run.” #57 says “latest completed run,” and the #57 and #62 plans use the most recent completed workflow on the exact current main SHA. A pending rerun on that SHA can leave the prior completed verdict eligible. This matches the recorded story behavior and the Done-when test. Disposition is accept the implemented policy and propose a wording clarification. [Epic R4](epic-triggers.md#requirements), [#57 requirement](tickets/story-run-reports-run-finished-and-main-green-with-images.md), [#62 plan](tickets/story-end-to-end-suite-over-the-epic-s-done-when-plan.md), [verdict selection](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/src/plotroom/core/github_projection.py#L285)

**Verification gap.** The writer rule restricts trigger cancellation to the orchestrator. The cancellation denial matrix exercises worker, clerk, and drain principals. The end-to-end suite also exercises bridge denial for creation. Adding the bridge to the cancellation matrix would catch a future bridge-specific permission change. Disposition is defer as a nonblocking verification follow-up. [Cancellation rule](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/src/plotroom/core/catalog.py#L367), [denial matrix](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/tests/test_triggers.py#L330), [creation checks](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/tests/test_epic3_e2e.py#L290)

**Edge cases.** The bounded lens traced grouped predicates, event cursor and branch binding behavior, report ownership, durable action execution, lease expiry and replay, and depth checks. It returned no confirmed unhandled path. [Implementation ranges above](#epic-summary)

## Behavior verification

In a disposable archive of exact main, the Python 3.14 run passed 52 tests across the trigger end-to-end suite, trigger engine, action delivery, and transport contract. The dependency check passed. Python 3.15 independently passed 25 supporting predicate, restart recovery, and deployment tests. Both Python jobs plus image and Helm passed on [exact-main CI](https://github.com/mgd43b/plotroom/actions/runs/45532939244). Both Python jobs also passed on the [#154 implementation merge](https://github.com/mgd43b/plotroom/actions/runs/45214712018).

| Epic criterion | Observed evidence |
|---|---|
| The second PR merge fires the `all_of` send once across a server restart | The real daemon receives distinct merge observations around a restart. The test checks one durable trigger fire, one delivered action with the matching key, and an empty later lease. Both MCP transports passed. [Restart case](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/tests/test_epic3_e2e.py#L176) |
| Both delivery channels carry the action key | The real daemon sends through a scripted Switchboard participant and the drain stand-in over both MCP transports. Separately, #60 records one native delivery between two real Claude Code sessions, completion of the matching lease, no fresh work, and no duplicate after daemon restart. [Keyed delivery case](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/tests/test_epic3_e2e.py#L225), [real drain witness](tickets/story-claude-lane-outbox-and-its-drain.md#notes) |
| Free-text predicates are refused and trigger creation is orchestrator-only | The real daemon rejects free text and unauthorized callers, then accepts an authorized typed trigger. Both transports passed. [Authorization case](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/tests/test_epic3_e2e.py#L290) |
| A lane report fires `run_finished` and main readiness requires its image checks | The worker report causes a fire. Fixture observations hold readiness until the successful workflow and configured image checks apply to the same main commit. Both transports passed. [Report and main-image case](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/tests/test_epic3_e2e.py#L371) |

The supporting tests cover the remaining declared predicates, atomic fire and enqueue rollback, first-PR branch bindings, recurring event and idle behavior, list/cancel and snapshot counts, and mark and archive actions. The closed action vocabulary and typed predicates support the initiative's action and event-text constraints. [Predicate checks](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/tests/test_trigger_predicates.py), [engine checks](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/tests/test_triggers.py), [action checks](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/tests/test_trigger_actions.py)

## Previous-retro follow-through

No Epic 2 retrospective was present at the baseline, so there are no recorded items to trace from it.

## Action items

1. Test bridge-role cancellation denial in the existing trigger matrix. Proposed owner is the next trigger verification lane. Keep the existing orchestrator-only rule and verify the rejected call leaves the trigger open. This is a test follow-up for normal triage. [Cancellation test](https://github.com/mgd43b/plotroom/blob/75b30834c29e1be3c618af173d228b55c94e0ca5/tests/test_triggers.py#L330)

2. Clarify Epic 3 R4 to say “latest completed run on the current main SHA.” Proposed owner is the ticketer during an approved prose reconciliation. The implementation and acceptance tests keep the same completed-attempt policy. [Epic R4](epic-triggers.md#requirements), [#57 requirement](tickets/story-run-reports-run-finished-and-main-green-with-images.md)

## Decision records

No separate architecture decision record is proposed. The observed trigger and delivery trade-offs are already recorded by AD-10 and AD-15 and the epic's Notes.

## Acceptance verdict

**accepted-with-open-items** against the declared criteria. The observed runtime evidence satisfies all four criteria, including the agent-run native #60 check that the scripted suite leaves separate. Every native child is complete. The open items are the bridge-role cancellation test gap, the R4 wording clarification, and Matt’s pending decision to accept the #60 evidence or repeat the check by hand. The implementation enforces the role and uses the completed-attempt policy recorded in #57 and #62. The owner still determines whether to close the epic.

## Open questions

The dedicated live GitHub fixture and its image-build workflow await the owner actions recorded on #29 and #33. Those tickets hold their own live verification. Epic 3's story-by-story prerequisites and accepted #62 fixture scope govern this verdict.

## Assumptions

- The invocation selected Epic 3 and pinned review and local runtime evidence to `75b30834`.
- The machine verdict treats the frozen #62 local fixture contract and the documented #60 native witness as acceptance evidence. It does not treat #29 or #33 as a whole-epic gate.
- No native Epic 3 ticket is pending. The proposed entries in this report are the bridge-role cancellation test and clarification of R4. No ticket or architecture decision record was opened or modified.
