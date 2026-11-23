---
title: Report and retry local cleanup after token revocation
type: bugfix
ticket: 23
created: '2026-11-22'
status: built
baseline_revision: 'c33b1a9b070e287d5d3e6441df86bc2cccdb03c0'
route: full
route_source: auto
risk: medium
review: thorough
review_source: pinned
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

The instance file is authoritative once revocation commits. Report a local cleanup failure separately and let the same revoke command retry cleanup. Invalid and unsafe token files must produce controlled CLI errors and deny HTTP authorization without exposing credential contents.

## I/O & Edge-Case Matrix

| State | Expected behavior |
| --- | --- |
| Instance removal commits and the local file cannot be updated | The command reports the revocation and local failure. HTTP rejects the revoked bearer. |
| The local file becomes writable or valid again | Repeating the revoke command removes the stale local entry and succeeds. |
| An instance or local token file is malformed, unsafe, or too deeply nested | CLI reads fail with a configuration error. HTTP authentication fails closed for an invalid instance file. Errors omit token data. |

</frozen-after-approval>

## Code Map

- `src/plotroom/app/tokens.py` reads private files, writes atomic snapshots, and holds the instance and local writer locks.
- `src/plotroom/app/cli.py` uses configuration and operational exit codes and logs structured errors.
- `tests/test_tokens.py` covers offline administration and can reuse the HTTP server fixture for real authorization checks.

## Tasks & Acceptance

- [x] Validate file safety, decoding, and record shapes before a snapshot is used. Treat parser recursion as a controlled configuration error.
- [x] Preserve commit-first revocation. Expose local failure separately and retain idempotent cleanup for already-revoked IDs.
- [x] Cover local validation and write errors, retry, live HTTP denial, and redaction with fixtures.
- Given the command is invoked with `--no-local`, when the instance record is removed, then local data and locks are untouched.

## Implementation Notes

- The CLI returns a configuration error for invalid local files and an operational error for local write failures. Both report the committed revocation and how to retry cleanup.
- The whole decoded registry must be valid before any credential is used. Errors identify the file or operation without quoting token data.
- The focused token and HTTP suites passed all 43 tests with locked Python 3.14.10. Ruff, Pyright, and the dependency check also passed.
- Both locked Python versions passed 434 combined tests. Native Codex review found no token-cleanup findings in its first pass.

## Plan Change Log

## Review Triage Log

- Follow-up low, fixed. Treat symlink refusal and invalid data as configuration errors. Keep operational read failures on the existing operational exit path.
- All 23 focused tests passed after the second review fixes across both stories.
- Three full native Codex review passes covered the batch. The combined suite passed 444 tests on each locked Python version after the token-file I/O fix.

## Verification

- `uv run --locked python -m pytest -q tests/test_tokens.py tests/test_serve_http.py`
- `uv run --locked ruff check src tests`
- `uv run --locked pyright`
