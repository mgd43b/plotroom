---
title: Scheduled project backups
type: feature
ticket: 4
created: '2026-11-24'
status: built
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

## Independent review dispositions

Retain the backup PVC when disabling the scheduler. A Job supplies its expected project and checks the result, so a misplaced bearer cannot make a run appear complete.

Bound long CronJob names with a deterministic hash suffix. The smoke resolves the rendered name through the release label.

Both locked Python versions
passed all 1,086 tests. Ruff, strict typing for both versions, and the dependency check passed.
The final locally built image passed all three fixture workflows before cleanup.

Opening review fixed a missing mounted backup directory. The daemon validates it before taking the data lock.

Closing the store waits for active backup workers before the daemon releases its data-directory lock. The controlled worker test verifies shutdown blocks until the copy finishes.
