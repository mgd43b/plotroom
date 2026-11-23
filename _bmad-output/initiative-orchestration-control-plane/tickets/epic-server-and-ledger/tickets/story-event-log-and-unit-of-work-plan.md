---
title: 'Event log and unit of work'
type: feature
ticket: 4
created: '2026-11-22'
status: built
baseline_revision: 'ec2d985e2170a09be12d71159845374a7f0118d0'
route: full
route_source: auto
risk: high
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md'
  - '{project-root}/_bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/tickets/story-tracer-plotroom-serve-answers-snapshot-over-http-and-the-std-plan.md'
  - '{project-root}/AGENTS.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The ledger has no write path. Every later story (inbox adapters, next items, actions, lanes) needs events that are typed, owned by their appender, unique per upstream fact and written one input per transaction by one writer per project, and mutating commands that survive a retry.

**Approach:** Build AD-2, AD-3 (writer part), AD-9 (the `seq`) and AD-14 in `core`, `ports` and `store_sqlite`: an event-kind registry with typed fields and appending roles; `events` unique on `(source, kind, upstream_id)`; one project `seq` counter allocated inside the transaction; a per-project writer task on one connection running one transaction per input; commands deduplicated by `request_id` and recorded as `core.<command>` events carrying their result; the writer started and stopped by `plotroom serve`.

## Boundaries & Constraints

**Always:** American English; public repo (no private names, hosts or secrets); no attribution lines; the implementer does not commit or push; `plotroom.core` imports only the standard library, `plotroom.ports` and itself (`tests/depcheck.py`); adapters never import each other; one Alembic revision on the single linear history (parent: the current head, `0001`); a duplicate append is a no-op that consumes no `seq` and runs no consequences; a failed input leaves nothing behind (events, `seq`, consequences all roll back); `seq` is one integer counter per project, allocated inside the writer transaction; ids are `evt_` + `uuid.uuid7()`; times are UTC RFC 3339 with `Z` from `Clock`, core's only time source; test with fakes and fixtures, run only changed test files with at most 4 workers (`pytest -n` is not installed: plain `pytest <files>`).

**Never:** tokens, the writer-rule table or role checks on commands (entry 5); next items, `ack`, `wait` (entries 6, 9); actions and `core.tick` (entry 7); adapters, `ingest` rules, adapter cursors (epic 2); `lane_id` forcing to a worker's current lane (needs lanes, entry 13 or later); changes to `src/plotroom/adapters/mcp/proxy.py`, `src/plotroom/app/cli.py` or their tests (another lane); a rerun of a failing test to get green; force-push or merge.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| New event | registered kind, allowed role, valid fields | stored with the next `seq`, id `evt_…`, `observed_at` from `Clock`; `created: true` | none |
| Duplicate event | same `(source, kind, upstream_id)`, any fields or body | the stored event returned, `created: false`; ledger and `seq` unchanged, no consequence runs | none |
| Same id, other kind or source | `(source, kind2, id)` | a separate event | none |
| Unknown kind, wrong role, bad fields | unregistered kind; role not in the kind's appenders; missing, unknown or mistyped field (bool is not int); `source` not the kind's prefix | refused, nothing written, `seq` unchanged | `LedgerError`: `invalid`, or `forbidden` for the role |
| Command, first call | registered command, role, new `request_id` | handler runs, `core.<command>` event with `upstream_id = <role>:<request_id>` stores args and result in the same transaction; result returned, `replayed: false` | none |
| Command retry | same command, role and `request_id` | the first result, `replayed: true`; handler not run; ledger unchanged | none |
| Same `request_id`, other role | | runs again (role is part of the key) | none |
| Command handler fails | handler raises `CommandError(code, message)` or any exception | transaction rolled back: no handler events, no `core.` event, no `seq`; the error reaches the caller; a retry runs the handler again | `CommandError` keeps its code; others propagate |
| Replay | a recorded input set (events and commands) applied twice | every table and the `seq` counter identical after the second pass | none |
| Concurrent inputs | many `submit` calls at once | each in its own transaction, in arrival order; `seq` values contiguous | none |
| Writer after failure | one input fails | the writer keeps serving the next | none |
| Caller cancelled | while queued / while its transaction runs | skipped / the transaction still commits or rolls back whole | none |
| Writer stopped | `stop()` drains queued inputs; `submit` after it | queued inputs complete; later ones fail `unavailable` | `LedgerError("unavailable")` |
| Server killed mid-transaction | SIGKILL with uncommitted rows already in the WAL | after restart no row, no `seq` of that input; earlier committed inputs intact; a new input works | none |
| Restart | close and reopen the file | events, `seq` and dedupe persist | none |
| Old SQLite | version below the floor | no project file opened, no writer started | existing behavior |

</frozen-after-approval>

## Code Map

- `src/plotroom/core/roles.py` (new) -- role grammar (`orchestrator`, `clerk`, `drain`, `bridge`, `system`, `worker:<slug>`), `role_family()`; rejects anything else so `<role>:<request_id>` is unambiguous.
- `src/plotroom/core/events.py` (new) -- `FieldSpec`, `EventKind`, `EventRegistry` (register, `validate_append(role, event)`), `NewEvent`, `Event`, `AppendResult`, `LedgerError(code, message)` (codes from `envelope.ErrorCode`), `format_time`.
- `src/plotroom/core/commands.py` (new) -- `Command(name, args, handler)`, `CommandError`, `CommandRegistry(events)`: registering a command registers kind `core.<name>` (appender `system`; fields `role`, `request_id`, `args`, `result`).
- `src/plotroom/core/unit_of_work.py` (new) -- `UnitOfWork`: the handle given to one input's work (`append`, `allocate_seq`, `role`); `append` validates, dedupes, allocates `seq`, inserts. It is the extension point for epic 2's cursor write and later consequences.
- `src/plotroom/core/writer.py` (new) -- `ProjectWriter`: one asyncio task, a queue of inputs, `submit(work)`, `start()`, `stop()`.
- `src/plotroom/core/ledger.py` (new) -- `Ledger(store, catalog, clock)`: `append(role, event)`, `execute(role, command, request_id, args)`, `run(role, work)`, `start()`, `stop()`, read helpers `head_seq()`, `events_after()`.
- `src/plotroom/core/catalog.py` (new) -- `Catalog(events, commands)` and `build_catalog()`: the one place later stories register kinds and commands (empty now).
- `src/plotroom/ports/store.py` (edit), `ports/clock.py` (new), `ports/__init__.py` -- `Transaction` (`find_event`, `insert_event`, `allocate_seq`), `Store.transaction()` (async context manager: commit on exit, rollback on any exception), `head_seq`, `events_after`; `Clock.now() -> datetime`.
- `src/plotroom/adapters/store_sqlite/migrations/versions/0002_event_log.py` (new) -- `events` (`seq` integer primary key, `id` unique, `source`, `kind`, `upstream_id`, `lane_id` null, `fields` JSON text, `body`, `observed_at`, `UNIQUE(source, kind, upstream_id)`), `project_seq(id CHECK id=1, value)` seeded 0.
- `src/plotroom/adapters/store_sqlite/store.py` (edit), `ledger_sql.py` (new) -- the writer connection (opened lazily, closed by `close()`), `BEGIN IMMEDIATE` through an execution option read by the existing begin hook (checked: `conn.execution_options(plotroom_begin="IMMEDIATE")` makes a second connection fail with "database is locked"), explicit commit, rollback shielded from cancellation; `SqliteTransaction`; reads on the engine pool.
- `src/plotroom/app/clock.py` (new), `app/serve.py` (edit) -- `SystemClock`; `Daemon` builds a `Ledger` per project after migration (never on the old-SQLite path), stops writers and closes stores before the lock is released, on the cancel path too (reuse `_finish`).
- `tests/` -- `fakes.py` (`MemoryStore` honoring the port incl. rollback, `FixedClock`, fixture command and kinds), `fixtures/event_inputs.json` (recorded inputs), `test_events.py`, `test_commands.py`, `test_writer.py`, `test_ledger_replay.py` (run over `MemoryStore` and `SqliteStore`), `test_store_sqlite_ledger.py`, `test_kill_mid_transaction.py` with child `tests/ledger_child.py`, `test_serve_ledger.py`. Edit `tests/test_migrations.py`: the two-heads and failed-DDL tests inject `0002` and assume head `0001`; derive ids and the parent from `head_revision()`.
- Do not change: `adapters/mcp/*`, `app/cli.py`, `core/snapshot.py` (cursor stays 0 until entry 6), the lock and floor code.

## Tasks & Acceptance

**Execution:**
- [x] `ports/`, `core/roles.py`, `core/events.py`, `core/commands.py`, `core/unit_of_work.py`, `core/writer.py`, `core/ledger.py`, `core/catalog.py` -- as in the Code Map
- [x] `0002_event_log.py`, `store.py`, `ledger_sql.py` -- schema and the SQLite transaction
- [x] `app/clock.py`, `app/serve.py` -- start and stop one `Ledger` per project
- [x] `tests/` -- every matrix row; the replay test compares a dump of all tables plus the counter; the kill test is deterministic (see Design Notes)
- [ ] story file: `status`, `Built: <PR>` note; this plan's status

**Acceptance Criteria:**
- Given a fixture command registered in the suite, when a recorded input set is replayed twice, then the second pass leaves every table and the `seq` counter unchanged.
- Given a command already executed, when it is retried with the same `request_id`, then it returns the first result and the handler does not run again.
- Given a SIGKILL during a transaction with uncommitted pages in the WAL, then the reopened file holds none of that input and a new input commits.
- Given `plotroom serve` stopped by cancel or by `stop()`, then every writer is stopped and every store closed before the data-directory lock is released.
- Given the dependency check, ruff, pyright (3.14 and 3.15) and the changed tests, then all pass.

## Implementation Notes

Approval: the lane brief fixes the scope and plan shape; Checkpoint 1 is not held for a human.

## Plan Change Log

## Review Triage Log

Quick lens, one pass, 7 findings. Verdicts: medium patched = 2, low patched = 4 (one bookkeeping), false = 0.

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Duplicate event with invalid fields refused, not returned | medium | patch | Matrix row "Duplicate event" says any fields or body; `append` validated fields before the lookup. Split identity checks from field checks |
| Command retry with invalid args refused, not answered | medium | patch | Matrix row "Command retry" and Design Notes: args not compared; `execute` validated args before the dedupe lookup |
| Story bookkeeping incomplete (`built` with no PR) | low | patch | Done by the lane after the PR exists (status and `Built:` note), as the tracer did |
| `events_after` limit -1 differs between stores | low | patch | Verified: SQLite `LIMIT -1` is unbounded, the fake slices `[:-1]`; both now return nothing below 1 |
| Re-entrant `submit` from work deadlocks the writer | medium | patch | Verified: `submit` queues behind the running item; later stories' handlers will hold a `Ledger`. Fails fast now |
| `stop()` re-raises the writer task's own cancellation, so `_close_projects` could skip stores | low | patch | `await shield(task)` raises `CancelledError` for a cancelled task; AC 4 requires every store closed before the lock goes |
| Two helpers named `_finish` | low | patch | Rename the store's to `_uninterrupted` |

## Design Notes

- Registry roles are role families (`system`, `orchestrator`, `worker`, …); `worker:<name>` matches `worker`. Event `source` must equal the kind's prefix (`github.pr_merged` → `github`). Fields: `str`, `int`, `bool`, `number`, `object`, `array`, each `required` or not; unknown fields are refused; values must be JSON.
- The `core.<command>` event is appended after the handler's own events (its result is not known earlier) and holds `role`, `request_id`, `args`, `result`. A duplicate returns the stored `result`; args are not compared (AD-2: a duplicate returns the original). Failed commands record nothing (AD-14).
- `observed_at` comes from `Clock`; `Event.fields` and `body` are stored verbatim (`body` is never parsed).
- The writer task owns the one connection: no other code calls `Store.transaction()`; a second concurrent entry raises. Reads (`head_seq`, `events_after`) use other pooled connections, which WAL allows.
- Kill test shape: `ledger_child.py` opens a `SqliteStore` and `Ledger`, commits one input, then runs a command whose handler appends many events with large bodies (several MB, past SQLite's page cache so uncommitted pages reach the WAL), prints `MID`, and awaits forever. The parent reads `MID`, asserts the WAL file is larger than its header, sends SIGKILL, then opens the file with plain `sqlite3`: only the committed input remains, the counter is at its value, and a fresh `Ledger` appends the next `seq`.
- Replay dump: `SELECT * FROM events ORDER BY seq` plus `project_seq`, via plain `sqlite3`, compared before and after the second pass.

## Verification

**Commands:**
- `uv run ruff check src tests && uv run ruff format --check src tests` -- expected: clean
- `uv run pyright --pythonversion 3.14` and `--pythonversion 3.15` -- expected: 0 errors
- `uv run python tests/depcheck.py` -- expected: exit 0
- `uv run pytest tests/test_events.py tests/test_commands.py tests/test_writer.py tests/test_ledger_replay.py tests/test_store_sqlite_ledger.py tests/test_kill_mid_transaction.py tests/test_serve_ledger.py tests/test_migrations.py tests/test_daemon.py` -- expected: all pass
