---
title: "Gate merges on main’s usable CI verdict"
type: feature
ticket: 2
created: '2026-11-23'
status: built
baseline_revision: 331a79b360c289137c42e71ee5ccd2890cd89c72
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - AGENTS.md
  - .claude/skills/clerk/scripts/lib.sh
  - _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/tickets/story-rule-3-main-s-verdict-and-the-marked-fix.md
---

<frozen-after-approval reason="human-owned intent">

## Intent

Determine the latest usable configured workflow verdict and recognize a fix label attributable to a configured human login.

## Boundaries & Constraints

Judge the newest completed workflow in the clerk’s window and fall through canceled, skipped and superseded runs. Authorize the fix exception only against red, with the rest of the gate still evaluated.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/ci.py` — Retain configured workflow identity and complete failed-job evidence.
- `src/plotroom/core/merge_gate.py`, `src/plotroom/core/merge_flakes.py` — Evaluate main color, excluding proven flakes, and validate current label provenance.

## Tasks & Acceptance

- [x] `src/plotroom/adapters/github/ci.py` — Retain configured workflow identity and complete failed-job evidence.
- [x] `src/plotroom/core/merge_gate.py`, `src/plotroom/core/merge_flakes.py` — Evaluate main color, excluding proven flakes, and validate current label provenance.
- [x] Add deterministic observations and acceptance cases under `tests/`.

Given startup failures, failed jobs, cancellations, unreadable jobs, unrelated workflows and confirmed flakes, when the gate queries main, then fixture outcomes match the clerk’s decision with proven flakes excluded. Given an attributable fix label, only main red is waived.

## Implementation Notes

- Apply the clerk’s 30-completed-run window to the configured workflow. Retain live default-branch run facts for existing consumers and distinguish unavailable jobs from successful empty or canceled evidence.
- Determine main red from startup failure or at least one unaccounted-for failed or timed-out job. An attributable fix label keeps evaluation moving to later rules.
- Keep the existing active label projection compatible and preserve the applying actor's type alongside the login. An authorized exception requires a human `User` actor and a configured login.
- Add explicit per-project GitHub policy settings for the selecting workflow, fix label, and authorized logins. Read the configured workflow’s completed history from the provider so a busy unrelated workflow cannot hide its usable runs.
- The #65 main flake proof is keyed by repository, failed head and Check Runs ID. Take the proof and associated workflow evidence from the same read snapshot. Fully explained main failures can fall through to the next usable run.
- The #65 proof interface is available in `core/merge_flakes.py`. Start with the consuming gate and store. Coordinate before editing `adapters/github/ci.py` and `adapters/github/details.py` while #65 finishes fixture verification.

## Plan Change Log

## Review Triage Log

- Native review, fixed. Configured main CI requires the default branch, same-repository head attribution and a non-PR event. Fork PR runs with the same branch name cannot clear main.
- Native review, fixed. A pending rerun preserves the exact preceding completed attempt or refuses with unreadable evidence. The gate cannot fall through to older green while a known failure is being retried.

- Medium, fixed. Configured CI ends at a decisive success or startup failure, and those states do not need job reads.
- High, fixed. Incomplete job states cannot clear a failed workflow or expose an older green verdict.
- High, fixed. Provenance polling checks beyond a full page when pagination metadata omits its successor. The late removal and reapplication fixture proves that the replacement actor wins.
- Medium, fixed. The label observation confirms the PR head and update timestamp after reading provenance. A concurrent PR edit invalidates the observation.

## Verification

- `uv run python -m pytest tests/test_merge_gate_main.py tests/test_merge_flakes.py tests/test_github_details.py` — scoped fixture contracts pass.
- The combined Rule 1, Rule 2, Rule 3, flake and GitHub detail fixtures passed 76 tests. Scoped Ruff lint, Ruff format and Pyright passed.
- After review corrections, the Rule 3 and GitHub detail suites passed 39 tests, and scoped Pyright was clean.
