---
epic: epic-briefs-and-checklists
date: 2026-11-25
verdict: accepted-with-open-items
criteria: declared
headless: true
---

# Briefs and checklists retrospective

## Epic summary

Epic #6 and BMad 6.1 through 6.6 (#95–#100) were recorded done and are closed as completed. All six plan statuses are built. Read the [epic](epic-briefs-and-checklists.md), [index](tickets/tickets.toml), all six stories, their six associated plans and the architecture constraints AD-8, AD-15 and AD-18 cited by the epic. No epic run folder or original session transcript was used.

- All plans share recorded baseline `65ed0835eeab89276f197f375cc1ebdcd87dfc54`. There is no defensible separate commit range for any of the tied baselines. The inferred aggregate end is [PR #159](https://github.com/mgd43b/plotroom/pull/159) merge `0b0e75fc4c0242ce8202dcc3164dfa0179d9ef0a`. The evidence script reported 20 commits, eight merges and five first-parent merges measured in that shared range. There is integrated peer work in the baseline range. The feature-specific first-parent merge patch spans 43 files, 2,496 additions and 126 deletions.
- The PR commits `809aead` (briefs), `2f2b49e` (checklists), `7d8511a` (identity and review fixes) and `92b6a5d` (bounded brief fetches and selected-step writes) define the attributable implementation and correction sequence. The [closure mirror #173](https://github.com/mgd43b/plotroom/pull/173) supplies the recorded parent status.

## Findings

- **Architecture, duplication and divergence — accept as-is.** The brief renderer uses the existing work-item adapter boundary and live lane claims. It returns text for the orchestrator to send. Checklist state uses typed rows in the writer and stores the PR identity at the point of use. State transitions and trigger actions share the archive proposal helper in `core/checklists.py` instead of creating separate item lifecycles. The largest churn entries in the raw baseline report include peer merge-controller work, which is outside #159's first-parent feature patch. [#159 diff](https://github.com/mgd43b/plotroom/pull/159/files)
- **Adversarial and edge-case review — accept the reviewed repairs.** The 6.2 and 6.4 plans and `7d8511a` preserve pending identity when branches are shared, prevent stale or unrelated merges from replacing an active checklist, and evaluate `checklist_empty` as a condition over completed active state. The 6.1 and 6.3 plans and `92b6a5d` record and verify the two automatic-review repairs, avoiding repeat materialization of an unchanged GitHub tree and writing only the selected checklist step. The refactor plan records its pre-review sweep and the targeted recheck. No new code correction is proposed from this narrowed review.
- **Verification gap — defer and retain evidence distinction.** #96 says activation follows a merge in the test repository and #100 says to run the suite against a real daemon and the test repository. The [end-to-end tests](https://github.com/mgd43b/plotroom/blob/0b0e75fc4c0242ce8202dcc3164dfa0179d9ef0a/tests/test_epic6_e2e.py) use a real local daemon with HTTP and stdio. The fixture pauses GitHub polling and passes open and merged facts through the ledger. It validates READY through archive and proves insertion and lifecycle behavior against those facts. It does not show that an actual repository delivered the merge. No specific actual test repository or corresponding run was established for these leaves. Future plans and closure evidence should say whether the merge was injected, mocked through the adapter, or observed live.

## Behavior verification

[Exact-main CI 45226874140](https://github.com/mgd43b/plotroom/actions/runs/45226874140) passed Python 3.14 and 3.15 on the #159 merge, attempt 1. The end-to-end brief case reads a materialized ticket, includes standing rules and claims from two other lanes, observes live claim release and denies a worker call. The checklist case exercises typed READY, a reported merge, named archive refusal, final-step proposal, and archive cleanup over both transports. The lifecycle suite verifies once-only `checklist_empty` behavior across restart. The populated migration test verifies `0016 → 0017` and preservation of existing state.

At the `75b30834` source tree, `uv run --locked pytest -q tests/test_claims_e2e.py tests/test_claims.py tests/test_epic6_e2e.py tests/test_checklists.py tests/test_checklist_lifecycle.py` passed 57 tests across Epics 5 and 6. No live provider merge or live send was performed for this retrospective.

## Previous-retro follow-through

The concurrent Epic 5 draft asks the owner to dispose of the stricter leaf repository criterion, asks the ticketer to retain older scope referrals, and supplies five reconstructed plans for review. The five local drafts exist by their canonical names. The evidence disposition and the larger process recommendation remain proposed. No previous Epic 5 retrospective existed at the baseline.

## Action items

1. **Ticketer and owner — proposed evidence disposition.** Determine whether #96 and #100 require a dedicated live repository run to supplement local proof. If it is required, identify the actual fixture before routing the run. Preserve all recorded completions pending that choice.
2. **Build and closure owners — proposed process follow-through.** Record the provenance of fixture-based merge facts wherever acceptance mentions an external repository. Keep the existing exact-main and local daemon checks and identify any separate live check explicitly.

## Decision records

No ADR is proposed. The epic's Notes already establish shared archive ownership and the active-checklist condition. The review trade-offs in the plans are linked to the commits above. No new policy was approved or written.

## Acceptance verdict

**accepted-with-open-items** for the declared Epic 6 Requirements and Done when. The parent brief and checklist lifecycle criteria are evidenced through both local daemon transports and exact main CI. The stricter leaf evidence question for #96 and #100 stays open, and remains distinct from known local behavior. This retrospective does not change historical completion.

## Open questions

Should an independently observed test-repository merge supplement the local fixture proof for #96 and #100, and which fixture would satisfy those leaves?

## Assumptions

Headless argument 6 resolves to this epic. All six tickets were finished before the review. The machine verdict applies the literal parent criteria and separately records the limits of evidence for the leaf wording. The selected inline diff scope covers lifecycle, identity and acceptance boundaries. Independent per-lens review and historical session analysis were unavailable for this run. The actions are proposals, with no new approval inferred.
