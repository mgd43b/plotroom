---
title: Safe migrations across project files
type: feature
ticket: 14
created: '2026-11-24'
status: in-review
baseline_revision: '38240710aa088a49d1bf9510fb51e34b9c4bba37'
route: full
route_source: auto
risk: high
review: thorough
review_source: pinned
---

<frozen-after-approval reason="approved migration safety scope">

## Intent

Before changing an existing SQLite schema, create a durable, private, online backup. Refuse newer or unknown revisions before opening the project for writes. Keep healthy projects serving when another database fails.

## Boundaries & Constraints

Existing revision 0024 remains the sole head. The data-directory lock and per-project writer rules still apply. Probe responses expose readiness without raw database errors. Preserve databases, WAL data, and stored events in older fixtures.

</frozen-after-approval>

## Code Map

- The SQLite migrate adapter owns Alembic inspection and upgrade. SqliteStore invokes it before the writer starts.
- Daemon startup owns project initialization and closes a failed store without interrupting successful projects.
- The existing project readiness routes reflect the assigned ledgers.

## Tasks & Acceptance

- Given two populated prior files and one corrupt file, when the daemon starts, then both usable projects remain queryable at head with pre-migration backups and the corrupt one reports unavailable.
- Given a newer revision or a failed backup, when starting migration, then the source schema and data stay unchanged.
- Given committed data in the WAL, when SQLite copies the database, then the backup includes it.
- Given a restart at the current revision, when probing readiness, then the daemon reuses the healthy files without making another backup.

## Implementation Notes

Existing SQLite files are inspected read-only before Alembic opens its writer connection.
SQLite's backup API copies committed WAL contents, and the completed private backup is published
before migration proceeds. A failed migration leaves its project unavailable while startup
continues for other projects. The schema stays at revision 0024.

## Plan Change Log

## Review Triage Log

- High, fixed. Publish the whole unique filename so later migrations can keep earlier backups.
- Medium, fixed. Sync the data directory as well as the backup directory before upgrading.
- Medium, fixed. Handle project-specific database failures during ledger initialization and
  close partial project resources. The fixture includes an intact SQLite file stamped at head
  whose application schema is incomplete.
- High, fixed. Isolate value-decoding errors from malformed stored trigger data.
- Medium, fixed. Read a real snapshot before starting a project's runtime so missing ledger
  state cannot produce false readiness. Both application-data regressions passed locally.
- Eight focused probe, migration, backup, and shutdown tests passed after these changes.

## Verification

- Run migration and HTTP fixtures plus both locked Python suites. Rebuild and check local Compose.
