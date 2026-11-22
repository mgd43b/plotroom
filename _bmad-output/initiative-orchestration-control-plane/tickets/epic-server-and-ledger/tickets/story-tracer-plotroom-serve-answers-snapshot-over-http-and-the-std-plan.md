---
title: 'Tracer: plotroom serve answers snapshot() over HTTP and the stdio proxy'
type: feature
ticket: 2
created: '2026-11-22'
status: built
baseline_revision: '46ff8ee555c11adaa49fda20f5e89e202336544d'
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

**Problem:** plotroom has no code, no CI and no transport. Every later entry needs a package, a gate-visible CI, a store with migrations and one tool surface reachable over HTTP and stdio.

**Approach:** Build the thinnest vertical slice through AD-1, AD-3, AD-4, AD-5, AD-7, AD-12 to AD-14 and AD-18: uv package, config loader, Alembic, `{ok, data}` envelope, JSON logging, `plotroom serve` (lock, SQLite floor, migrate, empty `snapshot()` over stateless HTTP) and a thin `plotroom connect --stdio`; CI on Python 3.14 and 3.15 whose check count is recorded for the clerk's gate.

## Boundaries & Constraints

**Always:** American English; public repo (no private names or hosts); uv-managed Python only; core imports only stdlib, `plotroom.ports` and itself; adapters never import each other; stdout of `connect --stdio` carries protocol only (logs go to stderr); no secrets in code, tests or logs.

**Never:** event log (entry 4), tokens and the writer-rule table (entry 5), `next`/`ack` (entry 6), `wait` (entry 9), other tools, container and `/healthz`, `/readyz` (entry 10), release and publishing (entry 11), per-project unavailability, backups and newer-schema refusal (entry 14), the all-tools contract test (entry 15), daemon auto-start and real-client spikes (entry 3); rerunning a failing test to get green; force-pushing or merging.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Empty snapshot | MCP client calls `snapshot()` over HTTP or through the proxy | `{ok: true, data: <empty board, cursor 0>}` | none |
| Second server | `plotroom serve` on a locked data dir | exits with the lock exit code and a JSON log line | no file touched |
| Old SQLite | version below the floor | server up, every tool returns `{ok: false, error.code: "unavailable"}` | logged |
| Failed migration | file with an unknown revision | `serve` exits non-zero before listening (entry 14 makes this per project) | logged |
| Several projects | more than one registered, no tokens yet | `snapshot()` returns `invalid`: tokens (entry 5) pick the project | none |
| Bad host | `Host` not in `allowed_hosts` | HTTP 421 | none |
| Proxy, server down | request with an id | JSON-RPC error response for that id on stdout | stderr log |
| Two heads | a second revision with the same parent | test fails | none |

</frozen-after-approval>

## Code Map

- `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `alembic.ini` -- package, tool config, managed Python, lock.
- `src/plotroom/core/` -- `envelope.py` (ok/err, codes), `snapshot.py` (pure empty board).
- `src/plotroom/ports/` -- `Store` Protocol (project id, readiness).
- `src/plotroom/adapters/store_sqlite/` -- engine, migrate (`migrations/` in-package, one linear history).
- `src/plotroom/adapters/mcp/` -- `server.py` (MCPServer + `snapshot`, Starlette app, host check), `proxy.py` (stdio proxy).
- `src/plotroom/app/` -- `config.py`, `logging.py`, `lock.py`, `sqlite_floor.py`, `serve.py` (wiring), `cli.py`.
- `tests/` -- one module per concern plus `depcheck.py` (AD-1 checker run in CI).
- `.github/workflows/ci.yml` -- matrix job per Python version: ruff, pyright, depcheck, pytest.
- `coordination/README.md` -- only the `ORCH_MIN_CHECKS` and `ORCH_CI_WORKFLOW` rows, after CI runs.

## Tasks & Acceptance

**Execution:**
- [ ] scaffold (`pyproject.toml`, tool config, lock) -- uv package, only-managed Python
- [ ] `core`, `ports`, `app/config.py`, `app/logging.py` -- envelope, empty snapshot, config loader and registry, JSON logs
- [ ] `store_sqlite` + migrations -- one revision, migrate via `run_sync`, single-head test
- [ ] `app/lock.py`, `app/sqlite_floor.py`, `app/serve.py`, `app/cli.py` -- lock, floor, wiring, `serve`
- [ ] `adapters/mcp/server.py`, `proxy.py` -- tool surface and proxy
- [ ] `tests/` -- every row of the matrix
- [ ] `.github/workflows/ci.yml` -- 3.14 and 3.15 with uv
- [ ] after CI: record both README settings rows; story status and `Built:` note

**Acceptance Criteria:**
- Given a running `plotroom serve`, when an MCP SDK client calls `snapshot()` over HTTP and through `plotroom connect --stdio`, then each gets the same empty board in the envelope.
- Given a running server, when a second `plotroom serve` starts on the same directory, then it exits with the lock error.
- Given CI on a PR, then ruff, pyright, the dependency check and pytest pass on 3.14 and 3.15.

## Implementation Notes

Approval: the lane brief fixes the scope and plan shape; Checkpoint 1 was not held for a human.

## Plan Change Log

## Review Triage Log

Quick lens, one pass, 19 findings. Verdicts: medium patched = 9, low patched = 6, false/already handled = 2, reject = 2.

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| README rows not recorded | false | none | Need a real PR run to count checks; done after CI |
| No serve-level failed-migration test | false | none | `test_a_project_file_that_cannot_migrate_stops_serve_before_it_listens` already added |
| Two-heads detector untested | medium | patch | `test_two_revisions_with_the_same_parent_are_detected` |
| CI cancels main runs | medium | patch | cancel-in-progress only for pull_request |
| CI commands differ from Verification block | low | patch | Verification block updated; pyright 3.15 run passed locally |
| Relative PLOTROOM_DATA_DIR resolved against file | medium | patch | env path is process-relative; test added |
| port coerces bool and float | low | patch | rejected; test added |
| Wildcard bind URL | low | patch | maps to loopback; port 0 left (test-only) |
| Cancel with null or unhashable params kills proxy | medium | patch | guarded; test added |
| Invalid UTF-8 kills proxy or hangs request | medium | patch | catch ValueError; test added |
| SSE ends without response, request hangs | medium | patch | error response emitted; test added |
| Reused request id loses cancel | low | patch | identity check; test added |
| uvicorn `color_message` field | low | patch | dropped in formatter; test added |
| SQLite URL by interpolation | medium | patch | `URL.create`; test with `?#%` path |
| Misleading env.py message and test name | low | patch | renamed |
| `__main__` unguarded | low | patch | guard added |
| test_logging leaks root logger state | medium | patch | autouse restore fixture |


Second pass, 9 inline PR threads from a review bot, all verified against the code and patched with tests: array or object request ids crashed the proxy (medium); multi-line responses broke stdout framing (medium); a stalled stdout blocked the event loop (medium); an empty or 202 answer to a request left it unanswered (medium); connection errors repeated the endpoint's userinfo and query (low); no pending-request bound (low); a literal `%` in the install path broke Alembic's script location (medium); invalid UTF-8 in the config file escaped as a traceback (low); the dependency check rejected an adapter's import of its own package through `plotroom.adapters` (medium).

## Design Notes

- Migrations live in the package (`adapters/store_sqlite/migrations/`), not the spine's root `migrations/`: a wheel must carry them. `alembic.ini` points there for `alembic revision`. Reported to the coordinator.
- Transport spike (SDK 2.3.0): stateless HTTP works for `Client(mode="legacy")` (initialize without version header, then `mcp-protocol-version` = negotiated version) and `mode="auto"` (every request stamped with `_meta` version; headers `mcp-protocol-version`, `mcp-method`, and `mcp-name` for `tools/call`). The proxy derives exactly those.
- CI gate facts: the clerk counts `gh pr checks` entries and reads the base branch's runs of `ORCH_CI_WORKFLOW`, so `ci.yml` also runs on `push` to main.

## Verification

**Commands:**
- `uv run ruff check src tests && uv run ruff format --check src tests` -- expected: clean (scoped: `.` would reach the skills under `.claude`)
- `uv run pyright --pythonversion 3.14` (and 3.15) -- expected: 0 errors
- `uv run python tests/depcheck.py` -- expected: exit 0
- `uv run pytest` -- expected: all pass on 3.14 and 3.15
