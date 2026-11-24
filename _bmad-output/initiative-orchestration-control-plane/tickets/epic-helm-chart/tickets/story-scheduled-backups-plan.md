---
title: Scheduled project backups
type: feature
ticket: 4
created: '2026-11-24'
status: in-review
baseline_revision: '05cf90f8d014c5072954cfd4e7f1b8765c31dedf'
route: full
route_source: auto
risk: medium
review: thorough
---

## Intent

Run scheduled SQLite backups through the server. Authorize one project per orchestrator token and
write to a separate mounted volume. Retain the configured number of completed copies per project.

## Acceptance and verification

- CronJob-derived Jobs run inside the disposable kind cluster and ask the server for both projects.
- Each copy reopens, uses the store migration entry point, and retains the fixture event.
- Three deliveries with retention two leave two files per project on the distinct backup claim.
- The focused HTTP and SQLite tests cover project scope, role checks, and retention.

## Implementation notes

The chart uses a read-only fixture token Secret for the scheduler. Only the server mounts either
PVC. The actual kind CronJob smoke and focused local backup tests passed.
