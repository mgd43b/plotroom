---
id: 4
type: story
title: "Rule 5: closing keywords"
parent: epic-merge-controller
covers: [R3]
after: [1, 2.5]
hitl: false
risk: low
tracker_id: "69"
remote: "https://github.com/mgd43b/plotroom/issues/69"
tracker_status: backlog
---

# Rule 5: closing keywords

## Description

A fetcher adds PR bodies and commit messages to the projection as structured fields; rule 5 refuses a closing keyword with an issue number in either, as in safemerge.sh, unless a keywords-ok label from a configured login is on the PR.

## Requirements

Verify: A PR with 'closes #1' in a commit is refused, one with 'Refs #1' is not, and the keywords-ok label from a configured login lets the first pass.

## References

- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-merge-controller/epic-merge-controller.md

## Notes

- Decision: 2026-11-22, PR bodies and commit messages are structured GitHub fields, not event body text, so reading them keeps AD-2; the after on 2.5 serializes the projection schema.
