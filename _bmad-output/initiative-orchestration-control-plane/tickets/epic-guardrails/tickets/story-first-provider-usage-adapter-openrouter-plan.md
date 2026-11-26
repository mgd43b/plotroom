---
title: First provider usage adapter (OpenRouter)
type: feature
ticket: 3
created: 2026-11-26
status: built
baseline_revision: 2c30e23c9064640c1952f411a4e22eb87086f811
route: full
route_source: pinned
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: ["{project-root}/AGENTS.md"]
---

<frozen-after-approval reason="human-owned intent">

## Intent

Read actual cumulative USD spend for a local budget from its dedicated OpenRouter inference key. Wire the read-only UsageMeter into daemon startup and the existing budget command and writer. Reconcile against the existing one-call trial. Refs #102.

## Boundaries & Constraints

- Read only `GET /api/v1/key` from the fixed official HTTPS origin. Use a capped, non-resetting key from a mounted file. Verify actual cap at or below the budget. Exclude BYOK and preserve the existing UsageMeter port.
- Bind each run to a uniquely pinned key in local configuration. Credentials and response identities stay out of records and logs. Direct Anthropic Console and further paid calls stay deferred.
- Keep the last amount and as-of through failed or lagging polls. Preserve complete corrections for other meters.

## I/O & Edge-Case Matrix

| Scenario | Expected outcome |
| --- | --- |
| Valid capped key bound to one configured run | Updates cumulative USD and as-of |
| Lower or equal delayed result or invalid metadata | Keeps committed amount and as-of |
| Duplicate binding, rotated file, or unconfigured run | Cannot attribute data to the wrong budget |
| HTTP, secret, or decoding failure | Keeps prior data intact with sanitized diagnostics |

</frozen-after-approval>

## Code Map

- `app/config.py` and `app/serve.py` parse and wire per-project run bindings with a mounted path and SHA-256 pin. Configuration establishes dedicated run ownership.
- `core/catalog.py` and `core/budgets.py` enforce configured registration and monotonic OpenRouter observations in one writer turn.
- `adapters/openrouter.py` polls registered budgets through the existing port. Reuse the existing mounted-file read without adding dependencies.
- `tests/test_openrouter.py` exercises normal startup with isolated HTTP. The disposable live check uses the prior trial key and GET only.

## Tasks & Acceptance

- [x] Wire configuration and registration for dedicated capped keys.
- [x] Validate attached key and cumulative metadata, and preserve spend and as-of through invalid or lagging data.
- [x] Verify deterministic adapter, startup, and secret handling. Document configuration.
- [x] Reconcile current same-key usage against the historical paid witness.

Given a configured OpenRouter budget, a valid higher counter updates through the normal tick path. Given invalid or stale metadata, committed spend and as-of remain fixed. Given another meter with a complete correction, its existing behavior is preserved.

## Implementation Notes

- Uses provider-specific monotonic treatment for lower and equal cumulative counters. Local observed time remains fixed until the counter increases.
- Uses the existing SQLite project metadata table for opaque reservations and checks earlier ownership across all project databases in the daemon data directory, including inactive projects. No raw key, configured pin, or provider identity is stored. Keep keys dedicated across separate data directories.
- Disposable same-key GET via the configured daemon and MCP returned USD $0.0000033, matching the historical response, generation, and key delta. No additional inference. The credential and private pin scan passed for database files, captured logs, status, and public implementation artifacts.

## Plan Change Log

## Review Triage Log

- High, patched. An updated file and pin could rebind an existing run, and removing a binding could allow historical key reuse. Added atomic per-project reservations and a read-only startup scan across project databases. Tests exercise both cases and transfer to another project after restart.
- Medium, patched. Isolated concurrent key polls under a per-key deadline so a slow key cannot discard healthy readings.
- Medium, patched. Catch malformed exponent and nesting errors, reject duplicate receipt fields, and exercise these cases with isolated transport.

- Low, patched. A verified empty project file has no historical key reservation and no longer disables configured metering. Unreadable or nonempty unexpected schemas still fail closed.

- Medium, patched. Archived lanes get a two-minute final polling window based on the durable archive receipt. Reservations outlive polling.
- High, patched. Recoverable target read locks skip only the meter sample. Tests cover tick progress, fatal errors, and cancellation.
- High, patched. Budget registration validates the mounted key and provider cap outside the writer transaction, after receipt and role checks. Failed verification leaves the key unreserved. Completed replays avoid another GET, and a concurrent committed receipt wins over a failed check.

## Verification

- Locked Python 3.14 and 3.15 affected tests, Ruff, Pyright, and dependency check.
- Disposable daemon acceptance on the prior paid witness using current-key GET and local credential-exclusion scans.
