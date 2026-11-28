---
title: 'Require completed Kodus review evidence in the Plotroom merge gate'
type: feature
ticket: 16
created: '2026-11-27'
status: built
baseline_revision: 'e39b03555ef69d0bda7cdaefc7f16e99db86abf5'
route: full
route_source: pinned
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Projects can require trusted Kodus review evidence before Plotroom offers or executes MERGE. Advisory, live, and freshly pinned reads use one decision rule.

## Boundaries & Constraints

**Always** — Bind evidence to the project repository, PR, exact head and configured App/check. Keep existing CI, thread, branch-update and merge-controller behavior. Cadence requires authoritative opening evidence, a completed opening review, a designated current skip, and complete resolved and answered threads.

**Opening authority** — Accept an optional project-admin attestation only for independently witnessed opening metadata. Bind the full SHA to the configured project repository, immutable GitHub PR ID, PR number and creation time. A missing attestation denies only the cadence path. Automatic capture is separate.

**Never** — Infer opening from first poll, age, public Events snapshots, commits, or private clerk files. No runtime activation, new secrets, deployment, live fixture writes, or publishing.

## I/O & Edge-Case Matrix

| Scenario | Evidence | Verdict with other gates clear |
| --- | --- | --- |
| Exact completion | Trusted run, completed success and exact completion summary on current head | MERGE |
| Cadence | Designated current skip, attested different opening head with genuine review, complete answered threads | MERGE |
| Unknown | Forged, missing, ambiguous, stale or partial review, opening, or cadence thread evidence | REFUSE |
| Default project | No review policy configured | Existing rules apply |

</frozen-after-approval>

## Code Map

- `src/plotroom/app/config.py` parses project GitHub policy.
- `src/plotroom/adapters/github/read.py` bounds authenticated provider reads. Add a review evidence adapter and call it from polling and fresh confirmation in `src/plotroom/adapters/github/details.py`.
- `src/plotroom/core/github_projection.py` and `src/plotroom/core/unit_of_work.py` can store a PR-scoped resource and trigger reconciliation without changing the schema.
- `src/plotroom/core/merge_gate.py` owns the shared verdict. Put review policy after existing refusal rules so Rule 4 keeps its precedence. `src/plotroom/core/ledger.py` and `src/plotroom/app/runtime.py` already revalidate pinned offers.

## Tasks & Acceptance

- [x] `src/plotroom/app/config.py`, `docs/features.md` — Validate and describe the opt-in identity and permitted provenance input.
- [x] `src/plotroom/adapters/github/` — Read complete bounded check and cadence evidence. Use direct check-to-PR proof. For completion with an explicitly empty association, require a bounded PR-scoped submitted review by the configured App bot, on the exact commit and inside the check run interval. Require direct association for skips. Refresh independent evidence for pinned confirmation.
- [x] `src/plotroom/core/merge_gate.py`, `src/plotroom/core/unit_of_work.py` — Consume current PR-scoped proof and refresh live offers on proof changes.
- [x] `tests/` — Exercise both permitted modes, stale/invalid data, default policy, Rule 4 precedence, and agreement across advisory, live, and execution paths.

Given an existing MERGE offer, when provider proof disappears or either pin moves, then pinned execution refuses. Given ordinary CI or thread refusal with valid review, when the gate runs, then the existing reason is preserved.

## Implementation Notes

- Persist the provider proof in a PR-scoped GitHub resource. A fresh check read and the ordinary check projection must agree on the trusted attempt. Cadence must also cover the ordinary current thread set.
- Pinned confirmation re-reads provider proof. Existing fixture dispatch evaluates the same rule with both pins and a fresh evidence revision.

## Plan Change Log

## Review Triage Log

- Medium, patch. An ordinary check refresh could reconcile after a same-ID summary changed but before the independent proof refreshed. Bind the proof to the check inputs in both reads and verify the transition.
- Medium, patch. A canonical provider URL with mixed-case owner or repository would reject a valid PR review. Compare the repository path case-insensitively with the origin and PR number still pinned.
- Medium, patch. Successful cadence was tested in the evidence reader and fixture dispatch separately. Exercise provider polling, advisory, live, and pinned confirmation together.

## Design Notes

The genuine #229 opening and final checks had empty PR lists. GitHub GraphQL associated the opening commit uniquely with #229. That establishes the provider's current association and cannot attest opening. The repository Events API's historical opened events returned the final head. The product's projected opened event represents first observation. Normal workflow runs record the event type without the activity action. A unique commit association can omit historical PRs. A submitted review by the configured App bot can corroborate completion when GitHub reports the exact commit under the target PR. A timestamp-correlated App comment without a commit pin cannot attribute completion. An empty-association skip has no submitted review to corroborate it.

## Verification

- Python 3.14.10 from the locked environment passed all 1,479 tests. Ruff lint and formatting, Pyright, and the core dependency check passed.
- Python 3.15.2 from the locked environment passed all 1,479 tests. Ruff lint and formatting, Pyright, and the core dependency check passed.
- Independent BMad review found three issues. The patches passed the focused regression runs and both full locked suites.
- After merge, verify both Python jobs on the exact main merge commit and rerun focused offline policy fixtures on that revision.
