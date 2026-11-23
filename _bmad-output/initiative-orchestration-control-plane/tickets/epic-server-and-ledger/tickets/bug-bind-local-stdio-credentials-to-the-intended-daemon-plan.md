---
title: Bind local stdio credentials to the intended daemon
type: bugfix
ticket: 21
created: '2026-11-22'
status: built
baseline_revision: '9a4db64961080a2987cf439f26ac6bc81efc3eee'
route: full
route_source: auto
risk: high
review: thorough
review_source: pinned
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

**Problem.** The local stdio proxy needs a verified link between its selected credential, the daemon's data directory, and the connection carrying each request. A listener's HTTP status alone cannot establish that link.

**Approach.** Pin the canonical data directory across local selection and startup. Publish a private Unix listener alongside public HTTP and require the connected peer's kernel PID to match the verified publication before writing HTTP.

## Boundaries & Constraints

Preserve explicit URLs, public HTTP/MCP, project authorization, revocation, graceful teardown, and the read-only lock PID probe. Use existing locked dependencies and controlled local fixtures. Older running daemons require a restart to publish the private listener.

## I/O & Edge-Case Matrix

| State | Expected behavior |
| --- | --- |
| A config edit or path alias changes after selection | The daemon stays on the canonical data directory selected by the proxy. |
| A socket peer differs from the publisher | The peer receives zero HTTP bytes. |
| A verified daemon responds to the bodyless readiness check with 401 | The proxy reuses the running daemon and forwards through a freshly verified connection. |
| A configured loopback alias has an allowed Host | The proxy uses the private publication and preserves that Host. |
| The daemon restarts or a connection closes | The proxy rediscovers the publication and verifies the next peer before forwarding. |

</frozen-after-approval>

## Code Map

- `src/plotroom/app/{cli,config,tokens}.py` loads connection settings and selects credentials under the canonical data directory key.
- `src/plotroom/app/{daemon_start,portfile}.py` starts the child, validates the published PID and kernel lock, and discovers its endpoint.
- `src/plotroom/app/serve.py` publishes the current listener and drains Uvicorn before releasing the data directory.
- `src/plotroom/adapters/mcp/{proxy,server}.py` forwards streaming requests and enforces Host and project constraints.

## Tasks & Acceptance

**Execution**

- [x] Canonicalize data directory selection and pin the on-demand child's data directory even when it rereads configuration.
- [x] Add a private, short Unix socket under an owner-only runtime directory and publish it with the existing PID and HTTP fields. Tie it to the data directory's device and inode, handle stale sockets, and close it with the HTTP listener.
- [x] Verify the Unix peer before HTTP writes. Use a bodyless readiness request and the same verified transport for MCP forwarding. Preserve Host handling and explicit client behavior.
- [x] Exercise each matrix row and existing real MCP client modes in the locked test environment. Document upgrade behavior.

**Acceptance criteria**

- Given a verified daemon with a revoked token, when the client's next call arrives, then the daemon still handles readiness and refuses the call.
- Given a pre-upgrade daemon owns the data directory, when local stdio attempts to connect, then it requests a restart and sends no HTTP traffic to that daemon.
- Given the daemon shuts down, when its lock becomes available, then its publication and private listener are gone.

## Implementation Notes

- The on-demand child checks the directory's device and inode before and after taking the lock.
- The Unix client verifies a fresh socket for every readiness check and MCP dispatch, including after restart. The public listener and explicit URL client retain their prior route.
- Python 3.14.10 passed 421 tests before the final path and runtime-directory checks. The follow-up selection passed 93 tests after those checks were added. Linux peer PID retrieval also passed in a local container without network access. Ruff, Pyright, and the dependency check passed.
- The combined batch initially passed 434 tests on each locked Python version. Native Codex review then found five issues in the listener implementation. The coordinator approved the scoped fixes below.

## Plan Change Log

- Reuse an open directory descriptor for the daemon's lock and publication. Register the private listener before accepting requests, and preserve public HTTP when the private listener is unavailable.
- Try the published Host after an alias gets a verified 421. Read legacy or stale publications without blocking on nonregular files.

## Review Triage Log

- Medium, fixed. Register the Unix server before awaiting start of acceptance. A controlled cancellation proves the socket is removed.
- Medium, fixed. Lock, publish, and unpublish relative to the same open directory. The replacement-daemon test proves another directory's publication and socket survive the original daemon's cleanup.
- Medium, fixed. Probe the published Host after a verified alias refusal and retain the successful Host for dispatch. A live daemon test uses different client and server allowlists.
- Medium, fixed. Public HTTP stays available when the private socket fails. Config-derived stdio reports that the private listener is unavailable and sends zero HTTP bytes.
- Low, fixed. A nonblocking open and regular-file check protect the newly shared fixed-port discovery path. Broader publication validation is a separate follow-up.
- All 33 focused listener, publication, signal, and lock tests passed after the review changes.
- The full suite then passed 441 tests on each locked Python version.
- Follow-up medium, fixed. Try the configured Host on the verified socket regardless of the connector's allowlist. The serving daemon decides which Host it accepts.
- Follow-up medium, fixed. Use a fresh private socket directory for each daemon startup. Normal shutdown removes it. A successor reclaims a stale socket directory referenced by its own locked publication.
- The full suite then passed 444 tests on each locked Python version.
- Final-review medium, fixed. Prefer the shared data directory or the owner's existing runtime directory when the socket path fits. Keep a short private temporary directory as fallback.
- Final-review low, fixed. Close the private listener and remove its directory before draining HTTP, so a forced stop during the drain does not strand the directory.
- Nineteen focused socket, listener, restart, Host, and shutdown tests passed after the two final changes. Ruff, both Pyright targets, and the dependency check passed.
## Verification

- `uv run --locked python -m pytest`
- `uv run --locked ruff check src tests`
- `uv run --locked ruff format --check src tests`
- `uv run --locked pyright`
- `uv run --locked python tests/depcheck.py`
