---
title: 'Spike: can plotroom read Claude desktop session state locally?'
type: chore
ticket: '1'
created: '2026-11-22'
status: built
baseline_revision: '46ff8ee555c11adaa49fda20f5e89e202336544d'
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

**Problem:** Epic 2's `SessionReader` port (entries 9 and 18, issues #43 and #44) needs to know whether a local process can see Claude desktop session turn ends and messages without the session calling `ingest()`.

**Approach:** Survey the app's local state and the documented interfaces read-only, write a stdlib-only probe under `spikes/claude-session-state/` that reads them and prints counts, key names and booleans only (never ids, titles, paths or message text), test it on a synthetic fixture, and record the finding in epic 2's Notes once PR #47 has merged.

</frozen-after-approval>

## Implementation Notes

Oneshot: one stdlib script, one test file, two ticket edits. Ticket and epic edits waited for #47 to merge. The plan file moved beside the ticket once the tree landed on main.

Spikes sit outside CI's ruff, pyright and pytest scope (`src`, `tests`); the probe was checked with the repository's ruff rules by hand and its tests run with unittest. Evidence gathered read-only from the local app: 16 live sessions, about 2,000 app-state files, 8 transcripts for turn identity, a 150 s poll with 5 busy to idle flips, and the documented hook and agents interfaces (docs agent).

## Verification

**Commands:**
- `python3 -I -m unittest discover -s spikes/claude-session-state -p "test_*.py"` -- expected: all tests pass
- `python3 -I spikes/claude-session-state/probe.py survey` -- expected: prints JSON counts only, exit 0
