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

Both locked Python versions
passed all 1,086 tests. Ruff, strict typing for both versions, and the dependency check passed.
The final locally built image passed all three fixture workflows before cleanup.

The opening Helm CI timeout occurred before a Ready server was observed. The next revision
preserved diagnostics and proved the root cause: with release and Service named plotroom,
Kubernetes injected PLOTROOM_PORT as a service URL. Plotroom expected an integer override and
exited. The chart disables service links on its pods; the CI-style local fixture uses the exact
plotroom release name and verifies authenticated replacement with that setting.

The Service-first smoke precreates and adopts the very same plotroom Service before any server or token pod exists. It checks the server has no PLOTROOM_PORT service override. The token Job uses a private one-off handoff file and the smoke verifies its logs are empty.
