---
title: 'The stdio proxy works with real Claude Code and Codex clients'
type: feature
ticket: 3
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
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The tracer's `plotroom connect --stdio` only proxies to a daemon someone already started. AD-3 says it starts the daemon, with a clean environment, when none runs, and reports the lock refusal; nothing yet proves two concurrent calls (one a long wait) or a cancel against a real server, or that real Claude Code and Codex sessions can use it.

**Approach:** Add on-demand daemon start (detached, minimal environment, readiness and failure reported to the client as JSON-RPC errors), prove envelope, concurrency and cancel against a real stateless server with a slow tool, then try the installed `claude` and `codex` CLIs headless with throwaway config and record what happened. A person runs the final check (hitl).

## Boundaries & Constraints

**Always:** American English; public repo (no private names, hosts or addresses); stdout of `connect --stdio` carries protocol only; no secrets in code, tests, logs or comments; tests that start a daemon or race a cancel wait on observable state, never on sleeps; never touch the user's real Claude Code or Codex configuration.

**Never:** tokens (entry 5), `wait()` and `plotroom watch` (entry 9), the transport contract test (entry 15), edits under `core/`, `ports/`, `adapters/store_sqlite/` (another lane), idle shutdown of the daemon, rerunning a failing test to get green, force-push or merge.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Daemon not running | `connect --stdio` with a config, nothing listening | daemon started detached with a clean environment; the first request is answered | none |
| Daemon already running | listener answers | no process started | none |
| Two clients start at once | two proxies, no daemon | one daemon wins the lock; both answer | the loser's lock exit code (4; was 3, which uvicorn also uses for a failed start) is not an error while the winner starts |
| Lock held, nothing answers at the URL | another `serve` owns the data directory on a different port | each request gets a JSON-RPC error naming the lock and the holder | stderr log |
| Daemon cannot start (bad config, port taken) | spawned child exits non-zero | each request gets an error carrying the child's logged reason | stderr log |
| Daemon dies mid-session | connect refused on a later request | one restart attempt, then the request is retried | error if the restart fails |
| Explicit `--url` or `PLOTROOM_URL` | any | never starts a daemon | connection error as today |
| Slow call plus fast call | `slow` pending, `fast` sent | `fast` answered first | none |
| Cancel a slow call | `notifications/cancelled` for it | its POST aborts and the server records the cancel | none |

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/mcp/proxy.py` -- `StdioProxy._forward` (add the readiness gate and one retry on `httpx.ConnectError`), new `UpstreamStartError`; keep the rest (envelope, SSE, cancel, limits) unchanged.
- `src/plotroom/app/daemon_start.py` -- new: `daemon_env`, `start_daemon`, `ensure_daemon`; wired from `cli._connect`.
- `src/plotroom/app/cli.py` -- `connect` builds the ensure callable only when the URL comes from the config; `--start-timeout`.
- `tests/slow_server.py` -- new helper: the SDK's stateless server with `slow` and `fast` tools and `GET /state`.
- `tests/test_proxy_server.py` (new), `tests/test_daemon_start.py` (new), `tests/test_serve_http.py` (reuse fixtures only).
- Reuse: `Daemon` and `DataDirLock` (the lock file holds the owner's pid), `Config.url`, `conftest.start_plotroom`.

## Tasks & Acceptance

**Execution:**
- [ ] `src/plotroom/app/daemon_start.py` -- start `python -m plotroom serve` detached (new session, no stdin, log file in the data directory) with only PATH, HOME, USER, LOGNAME, locale, TZ, TMPDIR and the closed `PLOTROOM_*` config list; poll the listener; read the child's logged reason on failure
- [ ] `src/plotroom/adapters/mcp/proxy.py` -- readiness gate shared by concurrent requests, failure answered per request, one restart-and-retry on connect refused
- [ ] `src/plotroom/app/cli.py` -- wire it for config-derived URLs only
- [ ] tests -- every matrix row, in both protocol eras where a request is sent
- [ ] real CLIs -- `claude` and `codex` headless against a throwaway config and data directory; record the outcome in Design Notes
- [ ] story file -- status and `Built:` note after the PR is green

**Acceptance Criteria:**
- Given no daemon and a config with a fixed port, when an MCP SDK client calls `snapshot()` through `connect --stdio --config`, then it gets the empty board and a daemon owns the data directory afterward.
- Given a slow call pending through the proxy, when a fast call is sent, then the fast answer arrives while the slow one is still pending.
- Given a slow call pending, when the client cancels it, then the server records the cancel and no response is relayed for it.

## Implementation Notes

Approval: the lane brief fixes the scope and plan shape; Checkpoint 1 was not held for a human. The real-client trial found that Claude Code's never-ending `subscriptions/listen` kept the proxy alive after the client exited; canceling listen streams at stdin EOF, and canceling every call once stdout is gone, were added to `proxy.py` beyond the Code Map for that reason. Two defaults went to the coordinator as a `Q:`, were approved and were built as written: the daemon outlives its proxy, and its clean environment drops adapter credentials.

## Plan Change Log

## Review Triage Log

Quick lens, one pass, 15 findings. Verdicts: medium patched = 6, low patched = 7, false = 1, accepted = 1.

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Real-CLI trial not recorded | false | none | Design Notes carry the table; written after the diff was staged. `Built:` waits for green CI |
| Matrix rows not run in both eras | low | patch | start, refusal, port-taken and explicit-URL rows now parametrized over both eras |
| `PLOTROOM_URL` untested | low | patch | `test_an_explicit_url_never_starts_a_daemon[environment]` |
| Foreign listener taken for the daemon | medium | patch | readiness is now an MCP `ping` answer, not a TCP connect; test with a socket held on the port. Surfaced that uvicorn exits 3 on a failed start, so `EXIT_LOCKED` moved to 4 |
| Concurrent refusals restart several times | medium | patch | the start each request relied on is retired once; `test_requests_refused_together_share_one_restart` fails without it |
| No respawn after the lock holder disappears | medium | patch | loop starts a child again when the one that lost the lock sees the lock free; test holds the lock in process |
| Start-failure reason useless | medium | patch | reason adds the traceback's last line and falls back to bare output; two tests |
| Scope beyond the Code Map | low | patch | the listen cancel and broken-pipe handling came from the real-client trial; recorded under Implementation Notes. BrokenPipe now handled in the write itself, and `run()` stops reading once the client is gone |
| `daemon_env` wider than the plan said | low | patch | plan text now names every key |
| `cli` imports the proxy at module top; `EXIT_LOCKED` duplicated | low | patch | `daemon_start` imported lazily; one constant |
| `--start-timeout` unvalidated | low | patch | positive finite seconds only; test |
| Flaky-test risks | low | patch | `RawProxy.close` kills a hung proxy; the gate test yields loop turns before asserting. `free_port` stays: it picks a free port and releases it before the daemon binds (a collision needs another process to take that exact port in between) |
| American English | low | patch | `canceled` in new names, comments and the server state |
| `slow_server` dead parameter, no way to release | low | patch | parameter removed; `POST /release` added |
| Progress on stdin EOF with other calls pending | accepted | none | in-flight calls other than listen streams still finish, as the tracer decided |

## Design Notes

- A server that answers an MCP `ping` at the config's URL, not the lock, says whether a daemon runs (a bare TCP connect would take any program on the port for it): a second `serve` is harmless (exit 3), so a start race needs no probe. A child that exits 4 means someone owns the lock; the proxy keeps waiting for the listener until the start timeout, then reports the lock's owner.
- A daemon started on demand outlives the proxy and is shared by every client of the data directory; it has no idle shutdown.
- Adapter credentials (`PLOTROOM_<PROJECT>_<NAME>`, AD-12) are deliberately not in the daemon's inherited environment.
- Cancel works on the stateless server in both eras: aborting a request's POST cancels the tool on the server (`tests/test_proxy_server.py`, against the SDK's server with a `slow` tool). The real daemon has only `snapshot()`, so a person's cancel check runs `python tests/slow_server.py <port>` behind `connect --stdio --url`; `GET /state` shows `cancelled`.

**Client spikes (real CLIs, headless, throwaway config and data directory, 2026-11-22).** Claude Code 2.1.323 (`claude -p --mcp-config <file> --strict-mcp-config --allowedTools mcp__plotroom__snapshot`) and Codex 0.169 (`codex exec --ephemeral --ignore-user-config -c mcp_servers.plotroom.command=… -c mcp_servers.plotroom.default_tools_approval_mode="approve" < /dev/null`; without the approval key Codex refuses the call with "requires approval, but approval policy is never"; without `< /dev/null` it waits on stdin).

| | Claude Code 2.1.323 | Codex 0.169 |
|---|---|---|
| Era | 2026: `server/discover` probe, then `prompts/list`, `resources/list`, `tools/list`, `tools/call`, every message stamped with `_meta` version, id strings like `server-discover-probe-1` | handshake: `initialize` (`2025-06-18`), `notifications/initialized`, `tools/list` with a `progressToken`, `tools/call` |
| Daemon on demand | started by the proxy on its first request; `snapshot()` returned the empty board | same |
| Daemon after the client exits | keeps running, shared | keeps running, shared |
| Long-lived request | opens `subscriptions/listen` (id `listen:0`) and never closes it; the proxy stayed alive after Claude exited until it learned to cancel that stream at stdin EOF (fixed here) | none |
| Cancel | interrupt (SIGINT) sends `notifications/cancelled` with `requestId` and reason `AbortError: user-cancel`; the server recorded the cancel; no proxy left behind | interrupt sends `notifications/cancelled` with reason `MCP request cancelled by caller`; same result |
| Notes | every `initialize`-less call works through the proxy | `notifications/initialized` is answered 202 with no body; the tracer logged that as an error, now silent |

## Verification

**Commands:**
- `uv run ruff check src tests && uv run ruff format --check src tests` -- expected: clean
- `uv run pyright --pythonversion 3.14` -- expected: 0 errors
- `uv run python tests/depcheck.py` -- expected: exit 0
- `uv run pytest tests/test_proxy.py tests/test_proxy_server.py tests/test_daemon_start.py tests/test_serve_http.py -n 4` -- expected: all pass (drop `-n 4` if xdist is absent)
