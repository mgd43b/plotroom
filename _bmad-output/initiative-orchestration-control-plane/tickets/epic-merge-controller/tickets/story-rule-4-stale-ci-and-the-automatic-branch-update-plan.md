---
title: "Detect stale CI and stage a fixture branch update"
type: feature
ticket: 3
created: '2026-11-23'
status: built
baseline_revision: 331a79b360c289137c42e71ee5ccd2890cd89c72
route: full
route_source: auto
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - AGENTS.md
  - .claude/skills/clerk/scripts/safemerge.sh
  - _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/tickets/story-rule-4-stale-ci-and-the-automatic-branch-update.md
---

<frozen-after-approval reason="human-owned intent">

## Intent

Detect file overlap using evidence pinned to both PR and main and stage one branch update per PR head when all required guards pass.

## Boundaries & Constraints

Use a durable action key and expected head. Recheck eligible state before dispatch. Keep dispatch behind the fixture-only write port and raise a local lane item on conflict.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/pull_comparison.py`, `src/plotroom/adapters/github/details.py` — Fetch pin-specific merge-base and changed-file comparisons, and reconfirm the current refs before publishing.
- `src/plotroom/core/merge_gate.py`, `src/plotroom/core/merge_overlap.py` — Use the same gate snapshot for dry reads and writer decisions. Refuse unreadable comparisons or overlap without an authorized waiver for the observed head.
- `src/plotroom/core/branch_updates.py`, `src/plotroom/core/unit_of_work.py`, `src/plotroom/app/runtime.py`, `src/plotroom/ports/github_write.py` — Persist the stable action in the writer and dispatch through a constrained fixture port.

## Tasks & Acceptance

- [x] Fetch merge-base and changed-file comparisons for the exact head and main tip.
- [x] Refuse unreadable comparisons and stale CI unless a configured login applied the active waiver for that head occurrence.
- [x] Persist the stable action in the writer and dispatch through a constrained fixture port.
- [x] Add deterministic observations and acceptance cases under `tests/`.

Given two overlapping-file observations for the same head, including across restart, when the fixture runner dispatches, then it performs at most one branch update and records one conflict item if the port returns conflict. Unknown files, stale evidence, red main and an active deploy window suppress dispatch.

## Implementation Notes

- Pin the comparison to full PR and main SHAs. GitHub’s compare response caps the first page’s file list at 300; treat responses at the cap as incomplete.
- Reuse the existing action table and its unique key. A fixture port must accept the action key and expected SHA, ensuring retry safety even when the runner restarts before recording the outcome.
- Leave the application’s real GitHub credential on the read side. Resolve a conflict item’s audience from the owning lane, falling back to the orchestrator when no lane matches.
- Keep fixture execution scoped to a same-repository, open PR targeting the projected default branch. A base or head movement invalidates the eligible verdict and requires current evidence.
- Reuse a common merge snapshot reader for SQLite queries and the writer-side staging decision. Read overlap evidence and revalidate eligibility at dispatch. Leave deferred actions available after a temporary guard clears and ensure unrelated queued actions can still progress.
- Use the same policy and gate for fixture staging, dispatch, and read-only queries. A main-red fix label waives the merge refusal only, so its PR still cannot trigger a branch update while main is red.
- Bind an authorized allow-overlap event to the observed head occurrence. Re-observing the same active label event after the PR moves does not authorize the new head. A new event must carry fresh human attribution.
- Rule 3 now exposes `determine_main_verdict` and `authorized_label` interfaces in `core/merge_gate.py`. Coordinate with the Rule 3 implementer before editing that module, `store.py`, `details.py`, or `pr_details.py`. Start with independent comparison and action modules while Rule 3 finishes its integration.

## Plan Change Log

- 2026-11-23: Reuse the current projection schema and existing action queue. The detail poller can supply evidence to a runner with an explicitly injected fixture port. A queued action rechecks both refs, retires on a complete moved or closed PR, and keeps temporary refusals pending.
- 2026-11-23: Label events seen at the first poll of a head establish its baseline. A waiver requires a new event observed against an existing baseline of that head and version, with attribution to a configured human login.
- 2026-11-23: Keep the key pending when the PR closes or moves before any effect. Recover an already recorded outcome through the fixture port in lookup-only mode. A same-head reopen can update once it is eligible again.

## Review Triage Log

- Native review, fixed. A recovery-only fixture lookup can reconcile a recorded conflict after main advances on the same PR head, including when the process exited before recording a local attempt. A deploy freeze defers all dispatch.
- Native follow-up, fixed. A failed receipt lookup defers the key without consuming a write attempt. The fixture verifies that a previously blocked action can still execute after eligibility returns.
- Final focused review, fixed. A successful lookup also avoids re-counting a previously recorded attempt. A receipt recovered after a crash supplies the first attempt when the local write count is zero.

- Medium, fixed. Compare provider repository names case-insensitively.
- High, fixed. Recover a known fixture conflict after PR identity changes without applying another update. Keep the originally checked main SHA and routing information.
- High, fixed. Preserve an unexecuted head key through closure or movement and resume after a matching eligible observation.
- Medium, fixed. Two polls stage one real fixture action and dispatch one successful expected-head update through the project runtime.

## Verification

- `uv run python -m pytest tests/test_branch_updates.py tests/test_github_details.py tests/test_actions.py` — scoped fixture contracts pass.
- All 27 scoped tests passed after those corrections. The deterministic transport verifies SHA-pinned comparisons, the 300-file cap, ref movement, temporary refusals, restart-safe conflict reporting, and progress of unrelated queued actions during a freeze.
- Owner verification of real write acceptance remains pending.
- Built: https://github.com/mgd43b/plotroom/pull/152
