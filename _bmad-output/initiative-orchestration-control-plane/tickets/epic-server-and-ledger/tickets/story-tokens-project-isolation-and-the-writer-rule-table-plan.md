---
title: Tokens, project isolation and the writer-rule table
type: feature
ticket: 5
created: "2026-11-22"
status: built
baseline_revision: c3c332031da8fee890af82a3dcf04ec82d991f8b
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: [AGENTS.md]
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Bind MCP calls to one project and role. Add offline token administration, live revocation, and a deny-by-default core table for ledger operations.

## Boundaries & Constraints

Keep the stateless MCP surface, catalog validation, duplicate semantics, daemon startup, and bounded logging. Use local fixtures. Credentials stay out of ledger data and logs. Later stories provide real commands, lane ownership, the drain and bridge workflows, and health endpoints.

## I/O & Edge-Case Matrix

| Scenario | Input or state | Expected behavior |
| --- | --- | --- |
| Project-scoped call | Project A or B bearer | Use only that project; no client-supplied project selector |
| Role authorization | Orchestrator and worker call an orchestrator-only fixture | Orchestrator succeeds; worker is forbidden |
| Deny by default | Registered command with no rule, undeclared event, or failed ownership predicate | Refuse before writing or replaying |
| HTTP gate | Bad Host or missing, invalid, or revoked bearer | HTTP 421 for Host, bare HTTP 401 for credentials |
| On-demand and already-running daemon | Config-derived stdio endpoint requires a bearer | Probe accepts 401; subsequent calls carry the selected bearer |
| Offline administration | Issue or revoke with no daemon, or while serving | Instance file stores hashes; local file is mode 0600; next request sees changes |
| Drain config | Per-project drain_session is absent, valid, or blank | No default; retain a valid session name; reject invalid values |

</frozen-after-approval>

## Code Map

- `src/plotroom/core/{catalog,commands,events,ledger,roles}.py` — share a writer-rule table across commands and event appenders. Check roles and ownership before command replay. Keep event identity checks before deduplication and field validation for new rows.
- `src/plotroom/adapters/mcp/{server,proxy}.py` — HTTP Host and bearer gate, scoped request state, and outbound Authorization. Keep the SDK transport checks and unchanged JSON-RPC bodies.
- `src/plotroom/app/{config,cli,serve,daemon_start}.py` — per-project drain session, offline token CLI, binding to the running ledgers, and readiness probing. Put token file operations in `app/tokens.py`.
- `tests/` — reuse subprocess helpers, the two protocol modes, and ledger fakes. Provision test credentials under a temporary home.

## Tasks & Acceptance

- [x] `src/plotroom/core/` — add shared writer rules and apply them to command execution and event identity validation.
- [x] `src/plotroom/app/` — add locked atomic token file mutations, private local storage, reload, config, and CLI wiring.
- [x] `src/plotroom/adapters/mcp/` — resolve the authenticated project and role per request and attach the bearer in the proxy.
- [x] `tests/` — prove both projects, role checks, deny by default, reload, file permissions, Host precedence, bare 401, and startup and logging compatibility.
- [ ] Story file — record implementation status and Built link. Hand off the real-client check and epic Notes.

Given a running two-project daemon, when each token calls snapshot, then the result identifies only its own project. Given a test tool backed by the real command path and two project ledgers, when a worker calls it, then neither project is changed. Given an already-running daemon and a revoked token, when a new proxy request arrives, then the original daemon is still serving and the request fails with a 401-derived JSON-RPC error.

## Implementation Notes

- `token issue --project <id> --role <role>` prints the bearer and its public ID. The instance keeps hashes in `instance-tokens.json`, and the proxy selects credentials from `~/.config/plotroom/tokens.json`. Both files use mode 0600. Use `token revoke <id>` to revoke the bearer and `--no-local` for instance-only administration.
- `connect --stdio --token-id <id>` selects a local credential when several exist. The proxy also accepts `PLOTROOM_TOKEN_ID`. An explicitly managed endpoint can use `PLOTROOM_TOKEN`.
- Ownership checks use the original arguments for a stored command receipt. A caller cannot replay a receipt for a target it no longer owns by sending different arguments.
- The focused pytest run passed 281 tests. The final serial suite passed all 397 tests on Python 3.14.10. Ruff lint and format, Pyright for 3.14 and 3.15, and the core dependency check passed.
- Matt's direct HTTP check in Claude Code and Codex is pending. The ticketer will record the observations in the epic Notes.
- After the review patches, 283 focused tests passed, including the local listener and `localhost` Host behavior. A loopback curl probe confirmed an empty 401 without `WWW-Authenticate`.
- All 401 tests passed in the serial suite after the review patches. Ruff lint and formatting, both Pyright targets, the core dependency check, and the diff check passed.

## Plan Change Log

## Review Triage Log

Quick review found one high and three medium findings.

| Finding | Verdict and route | Evidence and action |
| --- | --- | --- |
| Local bearer destination | High, patch | A live PID and port did not identify the listener on the host used by the proxy. Bind the credentialed URL to the actual published socket address. |
| Writes during ownership check | Medium, patch | The unit of work exposed append and sequence allocation inside a replay transaction. Give the predicate only the role and event lookup. |
| Issue publication order | Medium, patch | The proxy could select a local bearer before its authoritative hash was published. Save the hash first and roll it back on local write failure. |
| Bearer and URL userinfo | Medium, patch | HTTPX derives Basic authentication from URL userinfo and overwrites Authorization. Refuse both credential forms in one connection. |

## Design Notes

Identify credentials by a public ID so revocation needs no secret on the command line. Scope automatic proxy selection to the configured data directory; use an explicit bearer for separately managed URLs. A token does not select a project through the MCP payload.

Keep the production tool names unchanged. Use SDK `Context[Any, Request]` and ASGI request state for the identity and ledger. The fixture tool should use the adapter's command plumbing with the actual `Ledger.execute`. Preserve SDK checks for Origin, Content-Type, and body size. Protect credentialed requests to non-loopback endpoints with HTTPS and keep the proxy bearer out of the daemon environment. A data-directory-bound local credential must only go to that daemon's published port.

## Verification

- Run focused command, event, config, token, HTTP, and daemon/proxy tests with `uv run --locked python -m pytest`.
- Run the repository lint, formatting, type, dependency, and full-suite checks.
- Observe both CI matrix jobs and review threads on the PR head.
- Matt records real Claude Code and Codex behavior with a bare 401. The ticketer records the observations in the epic Notes.

### Real-client 401 check

Use the merged checkout. Start an isolated loopback daemon and wait for its `serving` log line.

```sh
plotroom_probe_dir=$(mktemp -d)
printf 'data_dir = "data"\nhost = "127.0.0.1"\nport = 0\n[projects.demo]\n' > "$plotroom_probe_dir/config.toml"
env -u PLOTROOM_HOST -u PLOTROOM_PORT -u PLOTROOM_PROJECTS -u PLOTROOM_ALLOWED_HOSTS \
  PLOTROOM_DATA_DIR="$plotroom_probe_dir/data" \
  uv run --locked plotroom serve --config "$plotroom_probe_dir/config.toml"
```

In a second terminal, replace `PORT` with the port from that log line. This probe expects HTTP 401, an empty body, and no `WWW-Authenticate` header.

```sh
plotroom_probe_url=http://127.0.0.1:PORT/mcp
curl --noproxy 127.0.0.1 -si -X POST -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"probe","method":"ping"}' "$plotroom_probe_url"
```

Start Claude Code with a temporary MCP configuration. Open `/mcp` and inspect `plotroom_auth_probe`, then exit the session.

```sh
plotroom_claude_config=$(mktemp)
printf '{"mcpServers":{"plotroom_auth_probe":{"type":"http","url":"%s"}}}\n' \
  "$plotroom_probe_url" > "$plotroom_claude_config"
claude --mcp-config "$plotroom_claude_config" --strict-mcp-config --setting-sources ''
```

Start Codex with a session-only MCP override. Open `/mcp` and inspect the same server and its startup diagnostics.

```sh
codex -c "mcp_servers={plotroom_auth_probe={url=\"$plotroom_probe_url\"}}" --no-alt-screen
```

Record both client versions, the exact connection messages, any visible sign-in or OAuth prompt or retry, and whether each session remains usable. Both clients connect over HTTP directly so they receive the bare response. Stop the daemon with Ctrl-C. The ticketer records both observations in the epic Notes before #16 closes.
