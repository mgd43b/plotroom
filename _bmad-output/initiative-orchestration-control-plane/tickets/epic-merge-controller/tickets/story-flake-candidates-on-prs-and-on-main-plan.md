---
title: "Record probable flakes with evidence"
type: feature
ticket: 6
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
  - _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/tickets/story-flake-candidates-on-prs-and-on-main.md
---

<frozen-after-approval reason="human-owned intent">

## Intent

Recognize a failing check that later passes on the same head, a PR failure outside the PR’s file set, and a main failure followed by a pass without a change to the test’s files.

## Boundaries & Constraints

Use conservative complete observations and stable identities per failure and test. Prefer structured evidence and retain check name and run URL when structured evidence is absent for a proven case.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/ci.py` — Project check histories, annotation or JUnit test evidence, and run links.
- `src/plotroom/core/merge_flakes.py`, `src/plotroom/core/catalog.py` — Detect and record stable candidates in the project writer, with a local next item.
- `src/plotroom/adapters/github/details.py` — Evaluate when relevant facts refresh and use main-commit comparisons to prove unchanged files.

## Tasks & Acceptance

- [x] `src/plotroom/adapters/github/ci.py` — Project check histories, annotation or JUnit test evidence, and run links.
- [x] `src/plotroom/core/merge_flakes.py`, `src/plotroom/core/catalog.py` — Detect and record stable candidates in the project writer, with a local next item.
- [x] `src/plotroom/adapters/github/details.py` — Evaluate when relevant facts refresh and use main-commit comparisons to prove unchanged files.
- [x] Add deterministic observations and acceptance cases under `tests/`.

Given a fixture for each supported pattern, when evidence is polled again or replayed after restart, then the system opens one item per candidate and preserves the evidence. A same-head fail/pass fixture without structured evidence uses the check name and run URL. All transports remain read-only for flake detection.

## Implementation Notes

- Use the Checks API's structured annotations as the evidence source. A complete terminal failure with no structured evidence may use its run link for the same-head fail/pass pattern. File-based patterns require known test paths.
- Use a stable cause identity in the event ledger before opening an item so an acknowledged candidate stays closed when the same failure is observed again.
- Read main check histories by exact SHA and require a complete comparison between the failed and later successful commits before treating their test files as unchanged.
- For a cross-commit main candidate, the successful check must publish a GitHub `notice` annotation with message `plotroom:test-outcome=passed`, the exact test ID in `title`, and the same repository-relative `path` as the failed test. The check-level same-head pattern uses the check's terminal pass.
- Use the existing read adapter and writer hooks. Keep comparisons bounded and reuse complete immutable SHA-pair evidence when available. The main verdict consumer needs to resolve a candidate by failure check identity, so keep that identity available in the event record.
- Retain the check-failure run link in projection details. For file-based evidence, ignore incomplete reads and require a concrete path. Define a bounded excerpt rather than copying whole annotation bodies.
- Bind PR file evidence to the observed head, version and update time, and confirm that the base ref and SHA stay fixed through pagination. Probe past full intervening commit-file pages when GitHub omits the next link.

## Plan Change Log

## Review Triage Log

- Native review, fixed. PR file collection now confirms the base ref and SHA across pagination, and the writer checks the PR head, version, and update time before recording a candidate.
- Native review, fixed. When a full intermediate commit file page lacks a next link, the fetcher verifies the terminal page. A reverted change to the test on a later page blocks flake proof.
- Follow-through on CI attribution. The auxiliary main-run feed can supply flake proof only for positively attributed main runs. A fork's otherwise matching pass cannot discount a main failure.
- Native follow-up, fixed. Persisting file provenance alone leaves the claims overlap reconciliation idle. Changed records or head bindings still trigger it.

- High, patch. A later green workflow job can pass while a test is deselected. Require an explicit structured pass for the same test and file before deriving proof across main commits.
- Medium, patch. Preserve structured evidence without an optional HTML link. The link is required for the check-level fallback.
- Medium, patch. The same failed check must keep its existing candidate identity when a provider supplies structured annotations after a check-level fallback was recorded.

## Verification

- `uv run python -m pytest tests/test_merge_flakes.py tests/test_github_details.py` — 20 passed on Python 3.14.10. Fixtures cover all three patterns, a green job without matching test proof, missing run URLs, acknowledgement followed by late annotations, unavailable annotations, incomplete comparisons, renamed or reverted test files, workflow identity, partial proof in a multi-test check, and acknowledgement across restart. The fixture permits only read requests.
- Ruff and Pyright passed for the changed flake implementation and tests.
- Flake and claims fixtures passed for the file-binding follow-up. They cover a base ref retarget, base SHA advancement, a same-version PR update before the writer commits, and a changed test file hidden after a full page without a next link.
- Main proof requires exact workflow and check identities plus a complete SHA comparison. Each poll considers at most 12 recent workflow runs and starts at most four comparisons. Each comparison inspects at most 10 commits and refuses an ambiguous file count at the provider cap. Completed comparison evidence persists across restart.
