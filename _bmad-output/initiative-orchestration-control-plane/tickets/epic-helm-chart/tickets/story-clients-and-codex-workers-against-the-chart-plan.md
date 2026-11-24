---
title: Wire chart values for local clients and the outside-kind bridge
type: feature
ticket: 3
created: '2026-11-24'
status: built
baseline_revision: 'ba65727a5f9adeb2258794518c8ba25d5ceeeba7'
route: full
route_source: auto
risk: medium
review: thorough
---

## Intent

Add exact opt-in Host authorities, explicit project bridge settings, and a bridge role for the
private one-off token Job. Keep the existing defaults, token design, and scheduler.

## Acceptance and verification

- Inspect the rendered and installed TOML for exact Host, board, bridge opt-in, and sender mapping.
- Verify an authenticated snapshot over the forwarded Service with the configured Host.
- Reject unlisted Hosts and wrong ports. Recheck the default kind token, restart, and backup smokes.
- Run the actual bridge process on the host and verify an inbound and outbound round trip against
  kind, including the scripted crash and redelivery check.
- Keep the ticket open for the separate actual Claude Code and Codex acceptance on merged main.

## Review dispositions

- Preserve absent bridge and token role values when upgrading an older release with
  --reuse-values. The actual kind upgrade retained the original bearer and default project.
- Reject empty bridge maps before issuing a bridge token. Parse ports in decimal and preserve
  literal trailing DNS dots and leading-zero ports.
- The default restart and backup checks, rendered Host rejection, actual outside-kind bridge
  round trip, and Compose regression passed. The full Python suites passed 1,088 tests each.
- This plan records the fixture wiring. Actual Claude Code and Codex acceptance remains with
  the separate verifier after merge.
