---
title: 'Close project files on daemon SIGTERM'
type: bugfix
ticket: 19
created: '2026-11-22'
status: built
baseline_revision: '79055c907c114c2edbcd35510f046707ef546346'
route: oneshot
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Uvicorn may replay SIGTERM after shutting down HTTP but before `Daemon.run` stops the project writers and closes their SQLite stores. First verify that behavior against current main with a real subprocess. If reproduced, let the normal daemon teardown complete before signal exit. Preserve the shutdown sequence, lock lifetime, port publication cleanup, cancellation behavior, and bounded logging.

</frozen-after-approval>

## Implementation Notes

The work is small enough for one pass. The HTTP test fixture synchronizes with a child daemon using its serving log and allocates a test-owned data directory and listener. `SqliteStore.close` closes the writer and disposes the connection pool. The regression checks for SQLite's last-close cleanup before any process reopens the project file.

- On the unmodified baseline, SIGTERM returned `-15` and the test failed because the populated WAL file still existed after exit.
- `Daemon.run` now owns Uvicorn's signal context from before listener startup through project teardown and lock release. The server keeps its existing HTTP shutdown, early removal of the published port, and SIGTERM exit status.
- The subprocess regression passed after the change. The serial daemon, HTTP, launcher, and logging test run passed all 107 tests.
- Local verification on Python 3.14.10 passed Ruff lint and format, Pyright, the core dependency check, and all 402 tests. Both Python CI jobs passed on the initial implementation commit.
- After GitHub review, the daemon now defers SIGTERM only. Uvicorn still restores the original SIGINT handler before project teardown. The new handler regression failed on the first implementation and passed after this change.

## Plan Change Log

## Review Triage Log

- Quick review found one medium-severity gap. The subprocess helper waited indefinitely after sending SIGKILL. Both waits now use the test helper's existing timeout.
- GitHub review found that deferring all Uvicorn signals also deferred Ctrl+C during project teardown. The daemon now defers only SIGTERM, and a regression test checks the SIGINT handler at store close.
- GitHub review noted that the port-file test asserts removal after exit. The early removal still happens before HTTP shutdown. The check during a paused shutdown was routed to refactor sweep #26.

## Verification

- `uv run --locked python -m pytest -q tests/test_serve_http.py -k sigterm` — exercise real SIGTERM and project close on the baseline and proposed change.
- `uv run --locked python -m pytest -q tests/test_daemon.py -k original_sigint_handler` — verify the original Ctrl+C handler is active while closing the project.
- `uv run --locked python -m pytest -q tests/test_daemon.py tests/test_serve_http.py tests/test_daemon_start.py tests/test_logging.py` — check teardown, lock, published port, and logging behavior.
- Run CI's Ruff, Pyright, dependency check and full pytest commands in the locked environment. Verify both Python jobs on the PR head and run the SIGTERM regression again after merge.
