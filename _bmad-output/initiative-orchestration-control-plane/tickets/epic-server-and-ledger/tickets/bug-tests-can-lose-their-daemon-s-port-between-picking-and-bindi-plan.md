---
title: 'Tests can lose their daemon''s port between picking and binding it'
type: bugfix
ticket: 18
created: '2026-11-22'
status: in-review
baseline_revision: 'd251a325b32079265efce6e07fe4dee53e706197'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Tests that start a daemon on demand pick a free port, release it, and hand it to the daemon, so another process can take it in between. The on-demand start refuses port 0 only because a client has no way to learn the port a daemon bound.

**Approach:** The daemon binds the port it is given (or port 0) and publishes the port it actually bound in a small file in its data directory; `ensure_daemon` reads it back for a config whose port is 0, and the proxy adopts the URL it returns. The suite starts every daemon on port 0 and keeps no pick-and-release helper.

## Boundaries & Constraints

**Always:** American English; public repo (no private names, hosts, addresses); a config with a fixed port behaves exactly as before; a port file is trusted only while the data-directory lock is held by the pid the file names; the daemon deletes any leftover port file right after it takes the lock and before it binds, and removes its own before it releases the lock; tests wait on observable state, never on sleeps; a flaky test is fixed, never rerun.

**Never:** edits to `core/`, `ports/`, `adapters/store_sqlite/`, `config.py`, `lock.py`, the HTTP middleware or the token CLI (lane #16); a port or address written into the lock file; a new dependency.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Daemon on port 0 | `serve` with `port = 0` | `plotroom.port` holds the pid and bound port before the `serving` line; it answers MCP there | none |
| Client, no daemon, port 0 | `connect --stdio`, config port 0 | daemon started; the proxy adopts the published URL and answers | a start failure is reported with its logged reason, as today |
| Client, daemon running, port 0 | live owner, matching port file | no process started; the published URL is used | none |
| Stale file after SIGKILL | file names a dead pid or a pid that does not own the lock, port serves MCP | never used; a new daemon is started and its URL used | none |
| Daemon dies mid-session | port 0, later request refused | one restart; the proxy adopts the new daemon's new port | as today |
| Two data directories at once | two port-0 configs | each client reaches its own daemon | none |
| Fixed port | config port N | `config.url` as before; port 0 is no longer refused | as today |

</frozen-after-approval>

## Code Map

- `src/plotroom/app/portfile.py` -- new: `PORT_FILE_NAME = "plotroom.port"`, `write_port(data_dir, port)` (JSON `{"pid","port"}`, temp file + `os.replace`), `remove_port(data_dir)`, `read_port(data_dir) -> tuple[int, int] | None` (pid, port; `None` when absent or malformed).
- `src/plotroom/app/serve.py` -- `Daemon.run`: `remove_port` right after `DataDirLock` is taken; `_Server.startup` writes the port before `set_result` (pass the data dir in); `remove_port` in the teardown `finally`, before the lock is released. Reuse `Config`, `_Server`, `wait_started`.
- `src/plotroom/app/daemon_start.py` -- `ensure_daemon` returns the URL (`str`); replace every `config.url` with a `current_url()`: `config.url` for a fixed port, else the URL built with `dataclasses.replace(config, port=bound).url` when `lock_owner(data_dir)` equals the file's pid, else `None`. Delete the port-0 refusal. Reuse `lock_owner`, `holds_lock`, `speaks_mcp`.
- `src/plotroom/adapters/mcp/proxy.py` -- `ensure` becomes `Callable[[], Awaitable[str | None]]`; `_ensure_ready` adopts a returned URL into `self._url` and `self._shown_url` (about 4 lines). Everything else unchanged.
- `src/plotroom/app/cli.py` -- `_starter`'s `start()` returns what `ensure_daemon` returns.
- `tests/conftest.py` -- add `held_port` (a bound, never-listening socket kept open for the test: connection refused, and no one else can take it); keep `write_config`.
- `tests/test_daemon_start.py` -- delete `free_port`, `write_fixed_config` and `test_a_port_of_zero_names_nothing_to_start`; use `write_config`; use `held_port` where a dead address is needed.
- `tests/test_proxy.py` -- `closed_port_url` (line 237) and the probe in `test_error_messages_never_carry_userinfo_or_a_query` (line 557) use `held_port`.
- `tests/test_daemon.py`, `tests/test_serve_http.py` -- port file tests; `tests/test_proxy_server.py` -- check for port picking.

## Tasks & Acceptance

**Execution:**
- [x] `src/plotroom/app/portfile.py` -- add the helpers -- one owner of the file format.
- [x] `src/plotroom/app/serve.py` -- publish and remove the bound port -- the daemon is the only one who knows it.
- [x] `src/plotroom/app/daemon_start.py`, `src/plotroom/adapters/mcp/proxy.py`, `src/plotroom/app/cli.py` -- read the port back and adopt it -- on-demand tests need no pre-picked port.
- [x] `tests/` -- convert every daemon start to port 0; add: published port equals the `serving` port and answers; the file is gone after stop and after a clean exit; a stale file naming a live foreign MCP listener is never used; SIGKILL then a new client reaches the new daemon on its new port; two data directories each reach their own daemon.
- [ ] Ticket file -- set `status`, add `Built: <PR>`, record a decision (port 0 now means "discover") in the epic notes.

**Acceptance Criteria:**
- Given a daemon started with port 0, when it is serving, then the port file's port equals the `serving` line's port and an MCP `ping` answers there.
- Given the suite, when searched, then no helper picks a port and releases it (`grep -rn "free_port\|closed_port_url" tests` is empty).
- Given a stale port file and a live foreign MCP listener on its port, when a client connects, then the foreign listener receives no request.

## Implementation Notes

- Port-file removal is also in `_Server`'s shutdown task, not only in `Daemon.run`'s teardown: after a SIGTERM uvicorn re-raises the signal as soon as it has shut down, so the process ends before `Daemon.run`'s `finally` runs (the file would otherwise outlive a `plotroom serve` that was stopped with SIGTERM; found when the first version of the stop test failed).
- `tests/conftest.py` `write_config` gained `port` and `name` parameters (defaults keep every caller as before); the two-configs-one-directory test needs a second, fixed-port config file.
- `daemon_start.current_url` is public (async, runs the blocking reads off the loop) so tests can call it directly. The failure messages for port 0 read: "it has published no address that answers" and no `at <url>` in the timeout message; fixed-port messages are unchanged.
- Risk, narrowed (`lock.py` is out of scope): `lock_owner` probes by taking the lock for an instant. `current_url` skips the probe when the file's pid is dead (the SIGKILL case), so only a stale file naming a live pid that holds no lock reaches it; a child starting in that window can lose the lock, exit with the lock code and be started again. It self-heals but logs one spurious "data directory is locked" line.
- Open: ticket file `status` and `Built: <PR>` wait for the PR, as with the other plans.

## Plan Change Log

## Review Triage Log

Quick lens, iteration 0: 1 medium, 4 low.

| Verdict | Route | Finding and evidence |
|---|---|---|
| medium | patch | An oversized pid in the port file made `os.kill` raise `OverflowError`, which escaped `current_url` before any daemon start, so every request failed until the file was deleted by hand. Confirmed; `_pid_alive` now treats it as dead, with a test. |
| low | patch | The port-0 failure message said "another address than this config's"; reworded, one line. |
| low | patch | The risk note overstated the lock-probe exposure; corrected. |
| low | defer-to-PR | Ticket `status` and `Built: <PR>` wait for the PR (plan task, not a defect). |
| low | rejected | No test for a clean exit of `plotroom serve`; stop, cancel and SIGTERM already exercise both removal sites, and a new test is more than a direct correction. |

## Design Notes

The port file, not the lock file or the log: `lock.py` and the `LockError` text parse the lock as a bare pid, and a hand-started daemon (and one whose log #90 rotates) writes no log the client can read. Pid plus lock ownership is the staleness check: a killed daemon's file names a pid that no longer owns the lock, and the kernel handing its port to another process cannot make the file trusted.

## Verification

**Commands:**
- `uv run ruff check . && uv run ruff format --check .` -- expected: clean
- `uv run pyright` -- expected: 0 errors
- `uv run pytest -q` -- expected: all pass
- `grep -rn "free_port\|closed_port_url" tests` -- expected: no output
