---
title: GitHub App credential renewal acceptance evidence
type: chore
ticket: 6
created: '2026-11-27'
status: built
baseline_revision: 'f43bf770150c8346766095b3e32fc0b2d0e6f43c'
---

## Implementation evidence

- [Built: PR #212](https://github.com/mgd43b/plotroom/pull/212), merged at `731e07b2c72f06582124eac780a1a4ac6569a9e0). It adds the file-backed App credential to the normal daemon and retains the PAT path when App settings are absent. The implementation includes fixture coverage for polling and renewal.
- The [public live witness](https://github.com/mgd43b/plotroom/issues/39#issuecomment-7218523340) ran on exact main `f43bf770150c8346766095b3e32fc0b2d0e6f43c` with [green main CI](https://github.com/mgd43b/plotroom/actions/runs/45937043552). The configured disposable daemon made two committed reads of the dedicated fixture PRs with distinct repository-scoped read-only tokens. A shortened adapter-visible lifetime and fake Clock drove renewal. GitHub's token expiries were unchanged and the second token had a later provider-reported expiry.
- The mounted key, App JWTs, and installation tokens were absent from the scanned configuration, captured logs, and project data. The fixture main tip stayed unchanged.

## Acceptance disposition

The owner accepted the local mounted-key run for #39. The canonical Notes retain the exact decision. Hosted runtime and CI-secret setup remain deferred. #122 retains its separate Kubernetes Secret acceptance and its existing prerequisites.
