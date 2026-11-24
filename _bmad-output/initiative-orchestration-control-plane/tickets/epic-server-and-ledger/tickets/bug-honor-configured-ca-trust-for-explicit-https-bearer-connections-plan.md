---
title: 'Honor configured HTTPS trust for explicit bearer connections'
type: bugfix
ticket: 22
created: '2026-11-24'
status: built
baseline_revision: 'bfe548f37742f95c95bb2b57806f1bd20a555a54'
route: full
route_source: pinned
risk: medium
review: thorough
review_source: pinned
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent">

## Intent

Approved choice: “Fix it now with local fixtures (Recommended)”.

Explicit HTTPS bearer connections honor configured CA trust and verify certificate chains and hostnames. Credentialed connections continue to bypass environment proxies.

## Boundaries & Constraints

Use loopback only, synthetic credentials, and fixture certificates. Preserve local daemon authentication, revocation, explicit HTTP restrictions, and the earlier stash.

| Scenario | Expected |
| --- | --- |
| Explicit HTTPS with fixture CA configured in SSL_CERT_FILE or SSL_CERT_DIR | Send bearer to verified origin |
| Default trust, unknown issuer, or mismatched host | Default validation remains enabled and fixture-only/unmatched certificate fails clearly |
| Environment proxy configured | Zero proxy connections for the bearer request |

</frozen-after-approval>

## Code Map

- `src/plotroom/adapters/mcp/proxy.py:StdioProxy` creates the credentialed client and forwards errors. Keep client proxy discovery disabled. Scope explicit TLS trust to the owned HTTPS bearer transport and surface TLS verification failures without credentials or sensitive URLs. Preserve the current local socket, revocation, and restart paths.
- `git diff cdf82a4^1 cdf82a4 -- src/plotroom/adapters/mcp/proxy.py tests/test_proxy.py README.md` contains an earlier read-only draft of the transport and loopback fixtures. Adapt selectively into current files. Do not apply or drop the stash.
- Add the CA configuration and direct-connection behavior to `README.md` alongside connect instructions.

## Tasks & Acceptance

- [x] Wire CA trust and certificate error handling in the explicit bearer path.
- [x] Test loopback TLS trust and default validation, mismatched hostname, proxy bypass, and unreadable CA behavior.
- [x] Run the current proxy and daemon transport tests to preserve regressions.

Given a loopback TLS daemon and configured trust, the bearer goes only to that verified origin and rejected TLS attempts send no authenticated MCP request.

## Implementation Notes

Adapted the read-only stash without applying it. Explicit HTTPS bearer transport reads the CA environment while the client disables proxy discovery. Both Python versions passed 160 proxy, daemon startup and transport checks, including the five loopback TLS cases.

## Review Triage Log

- Independent review verified that the client disables proxy discovery while the dedicated HTTPS transport loads the configured TLS trust. The loopback tests cover certificate identity, default trust, and the existing local paths.

## Verification

- `uv run --locked python -m pytest -q tests/test_proxy.py tests/test_daemon_start.py tests/test_transport_contract.py`
- `uv run --locked ruff check src tests` and `uv run --locked pyright`
- Combined independent review and rechecks completed. Full final suites passed 1,069 tests each on managed Python 3.14 and 3.15. Ruff, Pyright, core dependency and whitespace checks passed.
- [Built in PR #179](https://github.com/mgd43b/plotroom/pull/179)
