---
title: 'Recognize legacy Commit Status contexts in the merge gate'
type: bugfix
ticket: 17
created: '2026-11-27'
status: built
baseline_revision: 'c2e8a1bb04d42a1d8d0110450404cbce223d2835'
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

Rule 1 considers bounded legacy Commit Status evidence for the configured repository and exact PR head. Advisory, live offers, and execution revalidation share the same checks.

## Boundaries & Constraints

Preserve minimum and exact ignore-name policy, check producer identity, the existing gate order, repository and SHA pins, and cooldown. Keep status rows outside Kodus, JUnit, flake, workflow and trigger attribution. Use read-only fixtures. The scope excludes credential or permission changes, runtime activation, and tracker writes.

## I/O & Edge-Case Matrix

| Evidence | Expected behavior |
| --- | --- |
| Complete empty status set | Existing Check Runs decide Rule 1 |
| Current success and earlier failure from same producer | Latest status supersedes |
| Pending or unignored failure or error | Refuse |
| Same-name Check Run and status | Each can refuse and the name counts once |
| Unknown, stale, malformed, truncated, denied, or wrongly pinned status data | Refuse |

</frozen-after-approval>

## Code Map

- `adapters/github/read.py` provides bounded, verified pages. Use exact-SHA GET /commits/{sha}/statuses, a small explicit page cap, terminal probing for full pages, boundary revalidation, and strict row validation. GitHub returns newest entries first and context identity is case-insensitive. Validate each row's exact repository/SHA via its provider status URL, unique positive ID, known state, and actual creator ID. A bounded response with an empty final list is valid. The /status aggregate can call it pending and omits producer identity, so it supplies no gate facts.
- `adapters/github/details.py` provides projection polling and fresh confirmation. For PR heads, publish status and Check Runs resources in the same ledger transaction on success so a new result cannot combine with yesterday's still-fresh sibling. Refuse on either failed read, preserve rate-limit behavior, and keep flake data as Check Runs.
- `core/github_projection.py`, `core/unit_of_work.py`, and `core/merge_gate.py` provide the durable GitHub resource, merge wakeups, and shared evaluation. Keep statuses in a separate SHA-scoped resource and include its freshness in the verdict. Choose latest by case-insensitive legacy context and confirmed producer ID, leaving Check Runs supersession unchanged. Combine buckets before pending, minimum and failure decisions. Retain exact ignore names and count a shared displayed name once.
- `docs/features.md` records provider access requirements. GitHub provides public reads without additional permission and requires Commit Status read on private resources. Existing credential minting and permissions stay in scope as they are. Denied requests must hold the gate.

## Tasks & Acceptance

- [x] `adapters/github/` — Validate bounded real-shape responses, publish the exact head status set, and fetch it again during pinned confirmation.
- [x] `core/` — Require complete current evidence across shared Rule 1, live reconciliation and fixture execution. Existing Check Runs-only attribution remains intact.
- [x] `tests/` — Exercise the matrix, including same-source supersession, different producer and cross-source collisions, ignoring only failures, and provider denial or pagination. The daemon fixture must show advisory/live/pinned agreement and no intermediate stale partner verdict when both sources change. Keep existing fixtures representative by providing explicit empty legacy evidence, including fixture dispatch.

Given CI and legacy status data on the current head, when a pending or unignored failure is observed from either source, then a MERGE offer is withheld or withdrawn and execution refuses.

## Implementation Notes

- The current Check Runs set and trusted Commit Status generation commit together. The provider reader caps history pages, validates the current repository and SHA, and preserves creator identity. Focused provider, gate, and pinned controller cases passed on both interpreters.

## Plan Change Log

## Review Triage Log

- High, patch. A stable first page can advertise a later terminal page that disappears from intermediate Link metadata. Retain the provider's advertised bound and refuse a truncated list.
- Low, patch. The configured Kodus fixture had empty legacy evidence. Supply a successful legacy status with the trusted check's name and verify that Kodus still refuses.

- Opening Kodus review found two additional pagination cases. Explicit next pages cannot be empty, and a last page beyond the budget is refused at once.

## Verification

Built: [PR #234](https://github.com/mgd43b/plotroom/pull/234).

- Locked Python 3.15.2 full suite passed all 1,532 tests.
- Locked Python 3.14.10 full run passed 1,531 tests. The mounted-PAT smoke test exposed a preexisting snapshot/readiness race that was independently reproduced on the baseline tree. The exact test passed in a separate diagnostic run on the same candidate.
- Ruff lint and formatting, Pyright, and the core dependency check passed on both interpreters.
- Independent reviewers confirmed the fixes for every in-scope finding.
- Both locked Python versions passed 29 provider tests after the opening Kodus review corrections.
