---
title: Helm install runs Plotroom as one replica
type: feature
ticket: 1
created: '2026-11-24'
status: built
baseline_revision: '4f15cb9cd18f63c7a78c57bf921d4a4adc7c0b0c'
route: full
route_source: auto
risk: medium
review: thorough
---

## Intent

Install the existing image in local kind with one Recreate replica, a ConfigMap, a Service, and an
RWO claim for databases and tokens. Allow a one-off orchestrator token Job for the fixture.

## Acceptance and verification

- Lint and render the chart with the token Job enabled.
- A disposable kind cluster runs one ready server pod with its bound RWO claim.
- The token Job's bearer can read the project snapshot and write a fixture event.
- After deletion and replacement, the same bearer and event still work.

## Implementation notes

Helm lint, template, and the actual kind smoke passed on a locally built and loaded image.
CI runs the same smoke on a fresh kind cluster. The smoke captures the token privately.

## Independent review dispositions

Poll for a new Ready pod after deletion. Issue the fixture token through a post-install Job so image upgrades do not patch an immutable Job.

A Deployment can create a replacement Pod object while the old one terminates. The init container acquires the same data-directory lock before the daemon starts, so serving owners cannot overlap. The smoke checks init completion and allows the cursor to advance across restart.

Two full native Codex passes informed the review dispositions. Both locked Python versions
passed all 1,086 tests. Ruff, strict typing for both versions, and the dependency check passed.
The final locally built image passed all three fixture workflows before cleanup.
