---
title: Cover Epic 1 local regressions and Compose acceptance in CI
type: chore
ticket: 25
created: '2026-11-24'
status: in-review
baseline_revision: 'c239c33f67c5fce2d1911f6a60c14387e5722ed2'
route: full
route_source: auto
risk: low
review: thorough
review_source: pinned
---

<frozen-after-approval reason="approved local acceptance scope">

## Intent

Cover the five missing local acceptance cases for the Epic 1 sweep in the existing Python and container CI jobs.

## Boundaries & Constraints

Use disposable local fixtures. Preserve the established runtime policies and the existing authorization, restart and watch cases. The shared Compose smoke must run a real restart, keep its original bearer and durable event, and clean up its own project.

</frozen-after-approval>

## Code Map

- The daemon, tokens and commands tests already cover the adjacent lifecycle, registry and replay behaviors.
- The actions and lanes tests provide the failure fixture and authenticated MCP client pattern.
- The image job runs the Compose bridge smoke against a fresh named volume.

## Tasks & Acceptance

- [x] Assert that port unpublication precedes shutdown completion while the directory lock remains held.
- [x] Race two token issuers with distinct instance directories and verify both saved selectors and server-side identities.
- [x] Prove writer-rule withdrawal denies receipt replay without invoking the handler or writing.
- [x] Observe and close one failed action through the running daemon's authenticated MCP tools.
- [x] Verify project readiness, the original bearer and a persisted decision across a Compose service restart. Retain the existing bridge smoke.

## Implementation Notes

The regression tests exercise the existing runtime without production changes. The
Compose helper checks readiness, creates a decision with the original orchestrator
bearer, restarts the same service container, and checks that bearer, the retained
decision and its single persisted command event before running the bridge fixture.

## Plan Change Log

## Review Triage Log

The independent Codex review found two coverage gaps, both addressed.

- Synchronize both issuers at the shared registry lock and require kernel lock
  contention during the registry read. The prior start barrier allowed the
  shared updates to run sequentially.
- Keep the shutdown cancellation assertion tied to the test's own request.
  Fixture cleanup releases the pause even when an assertion fails.

The second pass identified a remaining registry gap. The regression now checks
that each issuer reads and writes within a single shared lock acquisition and that
the kernel lock is held at both steps.

## Verification

Run the focused tests, the locked Python 3.14 and 3.15 suites, and the image and Compose smoke with a unique local project. Verify the exact PR head in CI and repeat the Compose acceptance after merge.
