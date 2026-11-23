---
title: "Rule 5: closing keywords"
type: feature
ticket: '4'
created: '2026-11-23'
status: built
baseline_revision: 'b9bb69382b42edec2a4112113e278dade63a3dd8'
route: full
route_source: auto
risk: medium
review: thorough
review_source: pinned
lenses_ran: [native-safety, native-lifecycle, native-acceptance]
review_loop_iteration: 0
context: ['AGENTS.md']
---

<frozen-after-approval reason="human-owned intent">

## Intent

Project verified PR body and commit messages and apply Rule 5 with the clerk policy.

## Boundaries & Constraints

Preserve guard order, the deploy freeze, exact head and main evidence, and durable action safety. Use injected local fixtures for write effects. Keep the clerk's production gate outside the change.

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/github/pr_details.py` — Read structured PR and commit fields. Confirm head, update time, commit count and final SHA across pagination. Refuse incomplete evidence. Store the extra Rule 5 reads in dedicated resource attributes.
- `src/plotroom/adapters/github/details.py` — Refresh pull text as gate evidence for policy-enabled feeds. Keep incomplete text independent of the earlier Rule 4 branch-update path.
- `src/plotroom/core/merge_gate.py` — Add Rule 5 after overlap, using the exact clerk expression and current human label evidence for keywords-ok. Include the text observation in the watermark.

## Tasks & Acceptance

- [x] `src/plotroom/adapters/github/pr_details.py` — Read structured PR and commit fields. Confirm head, update time, commit count and final SHA across pagination. Refuse incomplete evidence. Store the extra Rule 5 reads in dedicated resource attributes.
- [x] `src/plotroom/adapters/github/details.py` — Refresh pull text as gate evidence for policy-enabled feeds. Keep incomplete text independent of the earlier Rule 4 branch-update path.
- [x] `src/plotroom/core/merge_gate.py` — Add Rule 5 after overlap, using the exact clerk expression and current human label evidence for keywords-ok. Include the text observation in the watermark.

- Given closing text, when judged with fresh complete evidence, then the gate refuses unless a currently authorized human applied the waiver.

## Implementation Notes

- The projection brackets paginated commit text with the pull identity, update time, body and commit count. The gate checks the body and each commit message independently to match the clerk expression.

## Plan Change Log

## Review Triage Log

- The word boundary differed under the dry runner’s C locale. The gate now uses a C-locale word boundary and the parity fixture covers a non-ASCII prefix.
- An active waiver could be removed while its provenance was read. The adapter now compares the active labels again before accepting provenance.

## Verification

- `uv run python -m pytest -q tests` — acceptance and regression suite passes
- Built: https://github.com/mgd43b/plotroom/pull/155
