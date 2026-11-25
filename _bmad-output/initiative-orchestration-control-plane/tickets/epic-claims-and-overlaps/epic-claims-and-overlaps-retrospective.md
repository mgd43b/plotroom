---
epic: epic-claims-and-overlaps
date: 2026-11-25
verdict: accepted-with-open-items
criteria: declared
headless: true
---

# Claims and overlaps retrospective

## Epic summary

Epic #5 and BMad 5.1 through 5.6 (#76–#81) were recorded done and are closed as completed. At the start of this pass, 5.1 had a built plan and 5.2–5.6 had no separate plans. The original 5.1 plan covers all six tasks together. The five new plans in this draft reconstruct each task from that record and the verified merge. No separate historical approval or baseline was found for those five plans.

- Read the [epic](epic-claims-and-overlaps.md), [index](tickets/tickets.toml), all six story files, and the original [shared plan](tickets/story-tracer-a-conflicting-claim-raises-an-overlap-item-plan.md), including its frozen scope, task checklist and review dispositions. Read architecture constraints AD-8, AD-16 and AD-18 as cited by the epic. No epic run folder or original session transcript was used.
- The shared plan baseline is `e09371505fef2d84711108c044f7ed7bfe862236`. The inferred cutoff is [PR #150](https://github.com/mgd43b/plotroom/pull/150) merge `3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0`. The baseline-to-merge evidence script reported 14 commits, five merges and three first-parent merges measured. The range includes peer work. Use the merge's first-parent diff for feature attribution, which spans 33 files, 1,782 additions and 51 deletions.
- The feature range contains `20169a7` (implementation), `74ec2c0` (review repairs), `097dcb8` (normalization and move cycles), `212a2bb` (Built links) and `11bdf4d` (automatic review follow-through). The [closure mirror #173](https://github.com/mgd43b/plotroom/pull/173) is status evidence, not implementation evidence.

## Findings

- **Architecture and integration — accept as-is.** The writer transaction reconciles claims and PR facts into the shared next-item lifecycle. `core/claims.py` and `core/pull_overlaps.py` own claim and PR-specific work respectively. SQLite and MCP sit behind their established boundaries. Archive and claim release converge on the same item lifecycle. The baseline churn report contains trigger work from the integrated peer lane. The exact #150 merge patch separates it from this feature and shows the claims and PR logic in separate modules. [#150 diff](https://github.com/mgd43b/plotroom/pull/150/files)
- **Adversarial and edge-case review — accept the reviewed repairs.** As merged, an edit cycle yields coordination without a contradictory recommended order, and normalized claims can be released by the stored spelling. `097dcb8` and `11bdf4d` implement and test those corrections in `tests/test_claims_e2e.py` and `tests/test_claims.py`. The shared plan referred same-head retarget identity. The later [#152 file-binding follow-through](https://github.com/mgd43b/plotroom/pull/152) confirms base ref and SHA across pagination and includes retarget and writer-revalidation fixture coverage. No duplicate candidate is proposed for that repair.
- **Scope and deferred referrals — defer.** The shared plan's Review Triage Log records local-filesystem root mapping, move-against-move and PR resource-read batching referrals. The canonical implementation uses a repository-relative GitHub ticket-tree source. No approved local root-mapping contract, distinct move-against-move requirement or measured batching problem was established in this pass. This retrospective retains those referrals for ticketer disposition. Keep branch-move and editor-cycle behavior distinct when evaluating any follow-up.
- **Verification gap — defer and retain evidence distinction.** #81 says the suite runs against a real daemon and the test repository. The [five tests](https://github.com/mgd43b/plotroom/blob/3ecf20da2ba64a6e5e2e18f4e41b50db05b3fab0/tests/test_claims_e2e.py) run authenticated against a real daemon, with the repository API supplied by HTTPX mock transport. #78's listed fixture-tree input is BMad 2.10. No evidence establishes that #81 ran against that remote or the dedicated repository under #29. The parent behavior has fixture evidence for every Done-when line. The stricter leaf evidence question remains. In future closure evidence, name the repository boundary and record any approved fixture substitution separately.

## Behavior verification

[Exact-main CI 45201121690](https://github.com/mgd43b/plotroom/actions/runs/45201121690) passed Python 3.14 and 3.15 on the #150 merge, attempt 1. The five local daemon cases assert that a PR overlap appears before review, a tree rename names editors and suggests order, a claim collision appears when it is made, filtered overlap and claim queries agree with the board, and worker versus orchestrator ownership is enforced. The merged tests cover the populated single-head `0012 → 0013` upgrade.

At the `75b30834` source tree, `uv run --locked pytest -q tests/test_claims_e2e.py tests/test_claims.py tests/test_epic6_e2e.py tests/test_checklists.py tests/test_checklist_lifecycle.py` passed 57 tests across Epics 5 and 6. This provides a fresh local behavior check without remote writes. The dedicated-repository leg was neither run nor credited.

## Previous-retro follow-through

No previous Epic 4 retrospective was found in the baseline, so no prior retrospective action list was available.

## Action items

1. **Ticketer and owner — proposed evidence disposition.** Confirm whether #81's test-repository wording needs a dedicated live run after fixture provisioning, or whether the documented local fixture is accepted for that leaf. Preserve completed status and scope pending the decision. No tracker write is proposed here.
2. **Ticketer — proposed process follow-through.** Keep the three deferred referrals above with the original review provenance. Draft a product or engineering ticket only if an owner resolves the missing scope or operational evidence. Preserve the later #152 resolution separately.
3. **Ticketer — proposed record repair.** Review the five reconstructed plans together. In future batched builds, leave one canonical plan record or explicit plan reference per story and preserve the shared baseline where it is genuinely known.

## Decision records

No ADR is proposed. The historical owner choice on claim roles and the epic's glob rule are already in canonical Notes. The review trade-offs above retain their actual plan and commit evidence. No new implementation decision was approved or written.

## Acceptance verdict

**accepted-with-open-items** for the declared Epic 5 Requirements and Done when. The five parent behavior checks and role/visibility constraints have daemon and exact-main evidence. The stricter #81 repository clause and the scope referrals remain open evidence or behavior questions recorded above. They do not change historical GitHub or BMad completion.

## Open questions

Which external repository proof, if any, should be retained for #81? Do the deferred referrals warrant their own scope after the first live fixture run?

## Assumptions

Headless argument 5 resolves to this epic. All six tickets were finished at the verified baseline. The machine verdict applies the literal parent acceptance criteria and preserves the separate leaf evidence gap. The selected inline diff scope covers the lifecycle, identity and acceptance boundaries. Independent per-lens review and historical session analysis were unavailable for this run. The three actions are proposals, with no new approval inferred.
