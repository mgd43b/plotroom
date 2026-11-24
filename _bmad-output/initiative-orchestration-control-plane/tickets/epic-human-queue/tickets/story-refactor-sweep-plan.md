---
title: 'Sweep Epic 7 deferred findings'
ticket: '7'
type: feature
created: '2026-11-24'
status: in-review
baseline_revision: '88e2387eb23faba0743a38189f5bf6a861f68b8d'
route: full
risk: low
---

<frozen-after-approval reason="human-owned intent">

## Acceptance

Given the six Built plans and the two merged PRs, when their named review findings and discussion are traced to disposition, then each of the eight inline review threads is answered and resolved and the plans carry no deferred or unfiled item. Given three split Markdown review tables, when the extraneous blanks are removed, then each reviewed finding remains in its table.

</frozen-after-approval>

## Tasks

- Read all six Built plans and GitHub review threads, replies, and PR-level reviews for #165 and #175.
- Preserve resolved behavior. Repair the three split review tables in the decision, ask-recording, and ask-check plans.
- Verify the resulting review records and the shared merge CI.

## Implementation Notes

Neither PR left an unresolved thread. #165 addressed exact ticket filtering, missing issues, deleted replies, batched closed-history reads, and the comment index. #175 addressed per-issue relationship retries, conditional boundary probes, and selective goal evidence. The six plans record the earlier native findings as addressed. Reference-only lane provenance is the documented goal behavior. No deferred finding requires re-filing. The formatting correction keeps the evidence in the corresponding review tables.

## Review Triage Log

## Verification

- [PR #165 queue evidence](https://github.com/mgd43b/plotroom/pull/165) contains five review threads with five replies and five resolutions.
- [PR #175 goal evidence](https://github.com/mgd43b/plotroom/pull/175) contains three review threads with three replies and three resolutions.
- All six preceding plans were inventoried. No deferred item or outstanding scope decision remained.
- `git diff --check` passed.

- [GitHub story](https://github.com/mgd43b/plotroom/issues/88)
