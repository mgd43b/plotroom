---
title: 'Read the daemon lock file without taking the lock'
type: bugfix
ticket: 20
created: '2026-11-22'
status: built
baseline_revision: d45517c6611018c8379c71aab356f158eb9881f9
route: oneshot
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

`lock_owner` briefly takes the daemon's exclusive lock when it reads an unlocked directory. A simultaneous daemon start can lose the lock to the probe and log `data directory is locked`. Read and validate the recorded PID without taking a lock or changing the file.

Given an unlocked file that records a live PID, when `lock_owner` runs, then it reports that PID, leaves the file intact, and issues no lock operation or lock event. Given a missing, invalid, or dead PID, the probe returns `None`. Proxy readiness and daemon refusal reporting still require evidence that the recorded process owns the directory lock.

</frozen-after-approval>

## Implementation Notes

- BMad one-shot covers the probe and focused regressions in `src/plotroom/app/daemon_start.py` and `tests/test_daemon_start.py`. Medium risk reflects the published listener check used with local credentials.
- `DataDirLock` in `src/plotroom/app/lock.py` continues to serialize daemon starts. Daemon handoff and published listener trust still use an explicit kernel-lock check.
- The PID report reads the file and uses signal 0 to check liveness. Listener discovery checks for matching live PIDs before trying the kernel lock and checks the recorded PID again before trusting the address. Timeout reporting also verifies the lock before naming another server as the active owner.
- The regression intercepts `flock` directly. Against the baseline, the interception caught an exclusive lock attempt before `lock_owner` could report the recorded PID.

## Plan Change Log

## Review Triage Log

- Native Codex review against `origin/main` completed with zero actionable findings. It traced PID validation, published listener trust, and timeout reporting. Sixteen focused tests passed in its sandbox. The repository runs covered the socket-based tests outside the sandbox.
- BMad quick review identified two retained risks. Readiness can briefly take the kernel lock when both stale files name the same live PID. Retained the existing kernel check under the coordinator's scope decision so a live PID alone cannot establish listener trust.
- BMad quick review also identified a pre-existing gap between daemon lock acquisition and PID publication. The old and new ownership checks can both see a held lock and the previous PID during that gap. The coordinator assigned daemon identity follow-up separately from #130.
- Kodus identified that a plain read of a named pipe can block the proxy past its startup timeout. The probe now opens read-only and nonblocking, checks the type of the opened file, and rejects records longer than 64 bytes. Added regressions for a named pipe with no writer and an oversized record containing a live PID.
- A second native Codex review found that Python optimization can skip a bare assertion in the FIFO test's child process. Replaced the assertion with an explicit nonzero exit so the child always calls the probe.

## Verification

- `uv run --locked --python 3.14 python -m pytest tests/test_lock.py tests/test_daemon_start.py`
- On Python 3.14 and 3.15, run locked dependency installation, `ruff check src tests`, `ruff format --check src tests`, `pyright --pythonversion` for the matching version, `python tests/depcheck.py`, and `python -m pytest`.
- Before the Kodus follow-up, the focused run passed 77 tests and both repository runs passed all 416 tests. After the follow-up, all 79 focused tests and the static checks on both Python versions passed.
- After the review's test-only change, the FIFO test passed with `PYTHONOPTIMIZE=1`. The 15 lock-related tests and Python 3.14 static checks passed.
