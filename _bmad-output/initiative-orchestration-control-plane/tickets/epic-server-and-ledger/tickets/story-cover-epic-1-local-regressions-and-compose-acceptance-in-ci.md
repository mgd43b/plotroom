---
id: 25
type: story
title: "Cover Epic 1 local regressions and Compose acceptance in CI"
parent: epic-server-and-ledger
covers: [R1, R2, R3, R6, R7, R8, R11, R12]
after: [10]
hitl: false
risk: low
tracker_id: "189"
remote: "https://github.com/mgd43b/plotroom/issues/189"
tracker_status: done
---

# Cover Epic 1 local regressions and Compose acceptance in CI

## Description

Add the three remaining specified local regressions from the refactor sweep and carry existing real-daemon and verified Compose acceptance into CI. Use the completed Compose story and its local image harness. The first release, deferred sweep findings, and the full post-release suite retain their existing gates.

## Requirements

- [ ] While the HTTP shutdown fixture is paused, assert that the daemon has already removed its port publication and still holds the directory lock. Extend `tests/test_daemon.py::test_cancelling_during_uvicorns_own_shutdown_waits_for_it_and_keeps_the_lock`.
- [ ] Exercise concurrent local-token updates from two daemon instances with separate data directories and one isolated local registry. Verify both records survive and can be selected by their own instance. Extend the same-instance and sequential separate-instance cases in `tests/test_tokens.py`.
- [ ] Record a successful command receipt, withdraw the applicable writer rule, and verify replay is refused without a new write. Extend the writer-rule and ownership replay cases in `tests/test_commands.py`.
- [ ] In a real local daemon fixture, use the existing failed-action setup to surface one item through `next()` and close it through `ack()`. Reuse `tests/test_actions.py` for failure setup and `tests/test_lanes.py` for the daemon client. Reuse existing lock refusal, lane authorization on both transports, exact `snapshot()` and `next()` equality across restart, and live watch wake coverage in `tests/test_serve_http.py`, `tests/test_lanes.py`, and `tests/test_watch.py`.
- [ ] In the existing CI image/Compose harness, check project `/readyz` and an authenticated snapshot, write a fixture event, then restart the Plotroom service on the same volume. Verify project readiness returns, the original bearer still works, and the event remains. Clean up the isolated fixture. Reuse the #183 Compose verification and leave the historical migration cases to their existing coverage.
- [ ] Run the existing Python 3.14 and 3.15 checks and the Compose image job. Keep the existing known-ID digest-mismatch and HTTP drain-role `snapshot()` denial cases in `tests/test_tokens.py` and `tests/test_transport_contract.py`.

## References

- Built: https://github.com/mgd43b/plotroom/pull/190
- parent — _bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md
- https://github.com/mgd43b/plotroom/issues/26
- https://github.com/mgd43b/plotroom/issues/27
- https://github.com/mgd43b/plotroom/pull/183
- Exact #183 main CI — https://github.com/mgd43b/plotroom/actions/runs/45490101083

## Notes

- Carry this proof forward to the refactor sweep (#26) and full suite (#27) at their existing gates. The first publication (#25), pending sweep findings, and full post-release verification remain in the original tickets. Writer-queue and backpressure policy are outside this story.
