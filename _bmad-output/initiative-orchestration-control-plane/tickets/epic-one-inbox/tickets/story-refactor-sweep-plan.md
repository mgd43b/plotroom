---
title: 'Epic 2 refactor sweep'
type: refactor
ticket: 12
created: '2026-11-27'
status: built
baseline_revision: '00f87df964b2059c4b5b3f8f27d80d41c9acc503'
route: full
route_source: auto
risk: low
review: thorough
review_source: pinned
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
review_loop_iteration: 0
context: [AGENTS.md]
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Disposition all eleven deferred comments on PR #48. Fix confirmed defects in the standalone Claude Desktop spike with synthetic inputs, and update two superseded local acceptance notes.

## Boundaries & Constraints

Only touch the spike, its tests, and the relevant Epic 2 story and plan evidence. Keep the probe standard-library-only and read-only. Use synthetic fixtures exclusively. Default output must omit actual session identifiers and message text. No production reader changes, real Desktop data access, issue mutations, or #46 work.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected output |
| --- | --- | --- |
| Mixed transcript | Truncated UTF-8, malformed assistant fields, invalid turn indices, unindexed peer and valid records | Preserve valid rows, reject malformed fields, surface the peer without an invented turn, omit raw values by default |
| Session selection | Registry ordering changes or the selected session disappears, and arbitrary/ambiguous transcript paths | Select by an opaque handle from survey, reject missing or ambiguous selections, and read only a contained exact transcript |
| Turn during read | Selected registry row changes or disappears during the read | Suppress the final report based on the stale idle observation |
| Watch measurement | Transcript shrinks or goes missing and timestamp is missing or malformed | No growth and null age |

</frozen-after-approval>

## Code Map

- `spikes/claude-session-state/probe.py` contains all CLI entry points. Stream binary JSONL rows, guard typed fields, and have survey accumulate statistics without keeping decoded rows or historical turn payloads.
- Use a stable opaque session handle in `survey` and `events --session`; keep ordinal presentation where useful. Use literal filenames, reject invalid session IDs, ambiguity and symlinks escaping the projects directory. Recheck the selected registry entry after reading before using the terminal idle result.
- `spikes/claude-session-state/test_probe.py` has the existing fixture and stdlib harness. Extend it for malformed rows, unindexed peers, stable selection and registry drift, path containment, stream behavior, and watch time/size.
- Two notes to reconcile are in `story-tracer-a-merged-pr-on-a-test-repository-reaches-the-projecti-plan.md` (#29 live run 45845942573) and `story-local-sessionreader-adapter-plan.md` (#44 real Desktop evidence PR #183).

## Tasks & Acceptance

- [x] Harden parser, survey and events against the deferred findings. Preserve known indexed-message/report behavior and measure unindexed peers without a guessed identity.
- [x] Harden session and path selection, recheck idle, and correct watch measurements.
- [x] Add focused synthetic regressions and reconcile the two notes.
- [x] Record each PR #48 comment ID and disposition here. The stale mechanism finding is already superseded by #44's canonical story and live evidence. Verify each remaining finding through code and a focused test or proven existing evidence.

Given the synthetic probe fixture, when survey, events, and watch run, then each affected observation is accurate and the default output omits private values. Given the eleven deferred review comments, when the sweep is reviewed, then each has a traceable disposition.

## Implementation Notes

The survey retains one active turn summary and accumulates counters as it walks the transcript. The CLI accepts the survey's opaque `session_handle`. The supplied Claude home is the trusted root. Transcript reads require a unique exact filename and check the project directories and opened file without following symlinks. A peer with an unknown index has `"turn": null`. The app-state report remains a separate source from the transcript's terminal idle decision.

## Plan Change Log

## Review Triage Log

| Deferred PR #48 comment | Disposition and evidence |
| --- | --- |
| [4660839701](https://github.com/mgd43b/plotroom/pull/48#discussion_r4660839701) | Superseded. The [canonical reader story](story-local-sessionreader-adapter.md) describes the mechanism and links merged [real Desktop evidence](https://github.com/mgd43b/plotroom/pull/183). The reader plan now records that evidence. |
| [4660839932](https://github.com/mgd43b/plotroom/pull/48#discussion_r4660839932) | Fixed. Binary JSONL iteration isolates interrupted UTF-8 to its row. `test_mixed_rows_are_safe_and_unindexed_peers_are_reported`. |
| [4660840146](https://github.com/mgd43b/plotroom/pull/48#discussion_r4660840146) | Fixed. Assistant message, text, and tool payload fields are checked before use. The mixed-row test retains valid reports. |
| [4660840345](https://github.com/mgd43b/plotroom/pull/48#discussion_r4660840345) | Fixed. Opaque session selection survives reordering and refuses a missing or ambiguous match. `test_events_handle_survives_reordering_and_rejects_missing_or_duplicate`. |
| [4660840546](https://github.com/mgd43b/plotroom/pull/48#discussion_r4660840546) | Fixed. Only positive, non-boolean integer indices are emitted. The mixed-row test covers string, object, boolean, zero, and negative input, including default redaction. |
| [4660840752](https://github.com/mgd43b/plotroom/pull/48#discussion_r4660840752) | Fixed. Transcript lookup validates the ID component, matches its name literally, and refuses ambiguity and escapes. `test_transcript_filenames_are_literal_and_paths_must_be_unique_and_contained`. |
| [4660840913](https://github.com/mgd43b/plotroom/pull/48#discussion_r4660840913) | Fixed. Survey counts and events emit peer rows with no valid index. The mixed-row test includes peers before any turn and while the latest turn is open. |
| [4660841131](https://github.com/mgd43b/plotroom/pull/48#discussion_r4660841131) | Fixed. The CLI rechecks the selected entry after reading the transcript before using idle to close the terminal turn. `test_events_suppresses_terminal_report_if_idle_observation_changes`. |
| [4660841288](https://github.com/mgd43b/plotroom/pull/48#discussion_r4660841288) | Fixed. Growth requires two known sizes and a strict increase. `test_watch_reports_only_real_growth_and_a_valid_status_age`. |
| [4660841435](https://github.com/mgd43b/plotroom/pull/48#discussion_r4660841435) | Fixed. Missing and malformed status timestamps give a null age. The watch test covers missing, string, boolean, zero, and a valid timestamp. |
| [4660841741](https://github.com/mgd43b/plotroom/pull/48#discussion_r4660841741) | Fixed. The survey releases historical payloads as it accumulates counters. `test_survey_counts_stream_without_retaining_historical_turns`. |

Independent review disposition:

| Lens | Verdict and disposition |
| --- | --- |
| Blind | Medium, patched. An unindexed peer interrupted report attribution. Added `test_missing_peer_index_and_malformed_position_end_attribution`, including an ordinary tool-result row. |
| Blind | Medium, patched. The reader now opens through no-follow directory descriptors and accepts only regular files. `test_reader_rechecks_regular_transcript_after_lookup` covers a changed file and directory. |
| Blind | Low, patched. Watch compares device, inode and size. The watch test includes a larger replacement. |
| Edge | Medium, patched. The regular-file check now validates the opened descriptor. The replacement test also covers a non-regular file. |
| Edge | Medium, patched. A malformed turn-position container stops attribution, with a separate fixture case in the attribution test. |
| Edge | Low, patched. Replacement cannot count as growth. |
| Edge | Low, patched. A future wall-clock timestamp gives a null age. |
| Verification | Low, patched. Added `test_watch_gap_breaks_observation_continuity`. |
| Verification | Medium, patched. Added `test_reader_yields_before_consuming_another_row` to test reader laziness independently from parser retention. |
| Verification | Low, patched. Added `test_boolean_app_state_edge_is_not_reported` to exercise both CLI paths. |
| Intent | Medium and low, patched. The peer attribution and future timestamp cases are covered above. Missing or ambiguous transcript paths suppress transcript reads. A valid session may still have an independent app-state report. |

## Verification

- `uv run --locked --python 3.14 python -I -m unittest discover -s spikes/claude-session-state -p 'test_*.py'` passed 21 tests.
- `uv run --locked --python 3.15 python -I -m unittest discover -s spikes/claude-session-state -p 'test_*.py'` passed 21 tests.
- Explicit Ruff checks passed for both spike files. The lint command uses `--extend-ignore PT009` for the standard-library unittest assertions. Normal CI excludes the spike.
- `uv run --locked --python <3.14 or 3.15> pytest -q tests/test_session_reader.py tests/test_intake.py` passed 13 tests on each interpreter.
- `git diff --check` passed.
