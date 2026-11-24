---
title: 'Recover failed local search writes within a bounded policy'
type: feature
ticket: 24
created: '2026-11-24'
status: built
baseline_revision: '976bc2958e57ebbdb4b693968cd6478ddc4cf889'
route: full
route_source: pinned
risk: high
review: thorough
review_source: pinned
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent">

## Intent

Approved choice: “Surface the failure and retry within limits from the last committed checkpoint (Recommended)”.

The daemon retries failed SQLite writes for the lexical search backfill and the embedding follower. Preserve writer transaction rollback, cursor checks, and per-project isolation. Surface initial, recovered, and exhausted outcomes using the existing structured daemon log.

## Boundaries & Constraints

Use fixture-only SQLite and loopback HTTP. Scope is local search. Preserve the writer, provider isolation and unrelated runtime behavior.

| Scenario | Expected |
| --- | --- |
| Temporary SQLite write failure after a partial batch | Roll back the transaction, log the error, and retry from the committed checkpoint |
| Persistent write failure | Bound retries and emit a visible terminal error for the affected input |
| Shutdown during retry | Cancel promptly; leave durable checkpoint safe |
| Valid foreground or other daemon inputs | Original behavior and retry policy |

</frozen-after-approval>

## Code Map

- `core/search.py` owns two writer submissions, and `app/serve.py` wires those two background inputs. Scope recovery to their search write boundary. Preserve provider retry behavior and avoid restarting unrelated project inputs or accepting partial commits. Use existing expected-cursor CAS (a replayed write is safe if commit outcome is uncertain).
- `app/logging.py` emits structured operator logs. Include project, source, attempt and a stable failure code; emit a clearly visible terminal error after bounded retries. Avoid logging SQL parameter values or source bodies. Preserve cancellation and avoid treating permanent programming or validation failures as temporary. Choose and document a small fixed retry schedule.
- `tests/test_search_backfill.py`, `tests/test_search_embeddings.py`, and daemon helpers cover existing rollback/restart. Inject actual deterministic SQLite write faults in the search input path, establish checkpoints and test same-run recovery and exhaustion. Update README with observable behavior.

## Tasks & Acceptance

- [x] Provide scoped retry and structured recovery reporting for both search background write inputs.
- [x] Test deterministic write failure after checkpoint with atomic rollback, same-run recovery, bounded exhaustion and cancellation without record loss or duplication.

## Implementation Notes

Approved choice implemented: “Surface the failure and retry within limits from the last committed checkpoint (Recommended)”.

- The daemon retries SQLite `BUSY`, `LOCKED`, and `FULL` for lexical backfill and embedding writes after 100 ms, 500 ms, and 1 second. Each retry submits the same cursor-checked transaction. The embedding follower reuses the provider outcome for that batch.
- Structured warnings, recovery notices, and terminal errors carry the project, source, attempt, and failure code. Reports omit database messages and parameters. Exhausted and permanent write faults stop the affected input.
- The store discards its writer connection on a failed commit to release any SQLite transaction that SQLAlchemy has deactivated. The next submission uses a clean connection.

## Review Triage Log

- Add SQLITE_FULL to bounded recovery and prove the real daemon wires both search inputs, preserves committed state after rollback, and serves results in the same process.
- Forward the production keyword from the existing Epic 7 daemon fixture so unrelated acceptance still covers the real backfill setup.

- Added exact SQLite `FULL` classification and an authenticated localhost test of both registered search inputs. The fixture forces `BUSY` after the lexical checkpoint write and `FULL` at embedding commit. Both inputs recover in the same daemon process.
- The opening review requested a failure from the writer's physical SQLite `COMMIT`. A deferred foreign-key violation on the writer connection surfaces as `SQLITE_CONSTRAINT_FOREIGNKEY` at commit, preserving the prior checkpoint and index rows. Explicitly resubmitting the batch succeeds on a fresh DBAPI connection.

## Verification

- `uv run --locked python -m pytest -q tests/test_search_backfill.py tests/test_search_embeddings.py tests/test_search_e2e.py tests/test_migrations.py`
- `uv run --locked python -m pytest -q tests/test_search_backfill.py tests/test_search_recovery.py`
- `uv run --locked ruff check src tests` and `uv run --locked pyright`

The focused suite passed all 72 tests on Python 3.14.10 and 3.15.2. Real fixture SQLite contention was injected after checkpoint SQL and at commit. Tests verified durable rollback, same-run recovery, exhausted retries, cancellation during backoff, and a redacted terminal error for a permanent constraint failure. Ruff lint and formatting checks and Pyright passed.
- Combined independent review and rechecks completed. Full final suites passed 1,069 tests each on managed Python 3.14 and 3.15. Ruff, Pyright, core dependency and whitespace checks passed.
- The backfill file passed eight tests each on Python 3.14 and 3.15 after adding the physical commit case. A negative control that retained the writer connection failed on the next `BEGIN` because SQLite still had the old transaction open.
- [Built in PR #179](https://github.com/mgd43b/plotroom/pull/179)
