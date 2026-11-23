---
title: 'Bounded server log'
type: feature
ticket: 17
created: '2026-11-22'
status: built
baseline_revision: '3841996709b5384242c2b85aa00e220d72916343'
route: full
route_source: auto
risk: low
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `plotroom-serve.log` in the data directory is the stdout and stderr of a daemon started on demand, so it only ever grows. A long-running daemon's log has no bound, and a size limit cannot be enforced from outside once the child holds the file.

**Approach:** The on-demand daemon is started with `serve --log-file <path>` and writes its JSON log lines through a size-rotating handler on that path, keeping a configured number of files. The limit and the number of files are config settings with defaults. A hand-started `plotroom serve` still logs to stdout.

## Boundaries & Constraints

**Always:** American English; public repo (no private names, hosts, addresses); each log line stays one JSON object; a log file never exceeds the limit unless one line does; the client's failure-reason read (`logged_reason`) still finds the daemon's last logged error; tests that need a rotation write past a small limit and read the files, never sleep.

**Never:** edits to `core/`, `ports/`, `adapters/store_sqlite/`, the HTTP middleware or the token CLI (lane #16); a new dependency; log rotation by an outside process; changing what a hand-started `serve` writes to stdout; rerunning a failing test to get green.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Write past the limit | `--log-file`, small `log_max_bytes` | the active file rolls to `.1`; logging continues in the new active file | none |
| More rolls than backups | `log_backups = 1`, three rolls | only the active file and `.1` exist | none |
| Defaults | no settings | 10 MiB limit, 5 backups | none |
| Bad setting | `log_max_bytes = 0`, negative or not an integer; `log_backups` negative | `ConfigError` naming the key | exit code 2 as for other config errors |
| Hand-started serve | no `--log-file` | JSON lines on stdout, no file written | none |
| Daemon fails to start | bad config or import error after the parent opened the file | the reason is read from the file as today, including after a roll | reads from the start when the file is smaller than the offset |
| Log path unwritable | `--log-file` in an unwritable directory | `serve` exits with a logged error on stderr | exit 1 |

</frozen-after-approval>

## Code Map

- `src/plotroom/app/logging.py` -- `setup_logging(stream, level)`: add an optional `file`, `max_bytes`, `backups` (a `RotatingFileHandler` with `JsonFormatter`, UTF-8, creating the parent directory) used in place of the stream handler; keep the stream behavior and the httpx quieting.
- `src/plotroom/app/config.py` -- `Config` gains `log_max_bytes: int` and `log_backups: int`; `_TOP_LEVEL_KEYS`, defaults `DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024` and `DEFAULT_LOG_BACKUPS = 5`, `PLOTROOM_LOG_MAX_BYTES` and `PLOTROOM_LOG_BACKUPS` overrides, validated like `_port` (an integer; limit at least 1, backups at least 0). Additive only; #16 owns the rest of the file.
- `src/plotroom/app/cli.py` -- `serve` takes `--log-file PATH`; `main` loads the config before `setup_logging` so the limits are known, and logs a config error to stderr with a plain stream handler when it cannot. Exit codes unchanged.
- `src/plotroom/app/daemon_start.py` -- `serve_command` adds `--log-file <data_dir>/plotroom-serve.log` (it needs the data directory: take the log path as a parameter); `_CONFIG_KEYS` adds the two new `PLOTROOM_*` names; `logged_reason` reads from byte 0 when the file is smaller than `since`. Keep the parent's stdout and stderr redirect to the same file (it carries output from before logging starts).
- `tests/test_logging.py`, `tests/test_config.py`, `tests/test_daemon_start.py`, `tests/conftest.py` (reuse `start_plotroom`, `write_config`).

## Tasks & Acceptance

**Execution:**
- [ ] `src/plotroom/app/logging.py` -- rotating file handler option -- the daemon owns the file it rotates.
- [ ] `src/plotroom/app/config.py` -- `log_max_bytes`, `log_backups` with defaults, env overrides and validation -- configured size and count.
- [ ] `src/plotroom/app/cli.py`, `src/plotroom/app/daemon_start.py` -- `--log-file`, the on-demand start passes it, the reason read survives a roll -- wiring.
- [ ] `tests/` -- unit: rotation keeps exactly `backups` old files, every line in every file is JSON, the newest line is in the active file; config defaults, overrides and rejections; integration: a real `plotroom serve --log-file` with a tiny limit and one backup rotates during startup, keeps only the active file and `.1`, and its last log line (uvicorn's shutdown line after SIGTERM) lands in the active file; a daemon started on demand with the settings in its config logs to the data directory's `plotroom-serve.log`; `logged_reason` after a roll.
- [x] Ticket file -- `status`, `Built: <PR>` after the PR exists.

**Acceptance Criteria:**
- Given a small limit and one backup, when the daemon writes past the limit three times, then only `plotroom-serve.log` and `plotroom-serve.log.1` exist and the daemon's later lines are in the active file.
- Given no settings, when a daemon is started on demand, then its log is `plotroom-serve.log` in the data directory and the existing on-demand tests still read its `serving` line there.
- Given a hand-started `plotroom serve` without `--log-file`, when it runs, then it writes JSON lines to stdout and creates no log file.

## Implementation Notes

## Plan Change Log

## Review Triage Log

Quick lens, iteration 0: 2 medium, 2 low.

| Verdict | Route | Finding and evidence |
|---|---|---|
| medium | patch | The rotation test's "nothing on stdout" assertion read a pipe the drain thread had already consumed, so it could not fail. Now checks the drained queue and `child.seen` after the child stops. |
| medium | patch | Two on-demand rotation tests read the log files while the daemon could still roll, a read-versus-rotate race. They now stop the daemon first. |
| medium | patch | A second `serve --log-file` that lost the lock wrote its refusal through a rotating handler on the winner's file. The loser now switches to plain stderr first; a test proves its line is not in the winner's file. |
| low | defer | `logged_reason` reads only the active file, so with a very small limit a failure line can roll into `.1` before the parent reads it. This is the behavior the plan specified; real limits (MiB) make it unreachable in practice. |

## Design Notes

The daemon rotates the file because only the writer can: a parent that opened the file for the child cannot roll it, and `RotatingFileHandler` renames the active file, so a second writer on the same path would follow the rename. The parent still points stdout and stderr at the file, but only for output from before logging starts (an import error) and a crash; after a roll those two descriptors follow the rolled file, which is acceptable for a handful of lines.

## Verification

**Commands:**
- `uv run ruff check . && uv run ruff format --check .` -- expected: clean
- `uv run pyright` -- expected: 0 errors
- `uv run pytest -q` -- expected: all pass
