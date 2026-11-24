---
title: 'Verify Epic 7 through the local daemon'
ticket: '8'
type: feature
created: '2026-11-24'
status: in-review
baseline_revision: '88e2387eb23faba0743a38189f5bf6a861f68b8d'
route: full
risk: low
---

<frozen-after-approval reason="human-owned intent">

## Acceptance

Given the isolated fixture repository and a real Plotroom daemon, when the orchestrator calls the MCP tools over HTTP and stdio, then verbatim decisions, four ask conditions, failure reasons, and durable read-only goals satisfy the four canonical Done-when lines.

</frozen-after-approval>

## Tasks

- Extend the shared localhost tool helper in `tests/test_epic3_e2e.py` to accept tools with a `name` argument.
- Add exactly one test for each Done-when line in `tests/test_epic7_e2e.py`. Use the existing local daemon and GitHub fixture routing, with real read adapters.
- Run the acceptance file on Python 3.14 and 3.15 and retain the full CI evidence.

## Implementation Notes

The suite runs the actual daemon and authenticated HTTP or stdio entry point, with deterministic provider responses from the same fixture repository harness used by the earlier epics. No live repository access is necessary for the stated checks.

## Review Triage Log

- Independent inspection found that a closed issue already assigned to the human could not prove the open-state selector excludes closed issues. The fixture now includes a separate unassigned closed issue in the selected milestone. The assigned closed issue remains a blocker fixture. Both runtime versions passed the revised suite.

- Native Codex review identified three gaps. The suite now rejects a quote found only in a different comment by the same human, polls a newer comment after its ask is recorded to prove burial on update, and uses a three-day configured threshold against a four-day-old ask to distinguish it from the seven-day default.

## Verification

| Epic check | Fixture evidence |
| --- | --- |
| Verbatim decisions and invalid citations | `test_decide_requires_verbatim_projected_human_words` |
| Four ask conditions surfaced through `next` | `test_next_flags_each_ask_condition_in_isolation` |
| Each remaining issue and its failing clauses | `test_goal_reports_exact_remaining_issues_and_all_failed_clauses` |
| Durable, read-only goals and unavailable evidence | `test_goal_survives_restart_and_reads_without_writing_with_uncertain_evidence` |

- `uv run --locked python -m pytest -q tests/test_epic7_e2e.py` passed all eight cases on Python 3.14 and 3.15. Including the neighboring Epic 3 and Epic 6 suites, 20 tests passed per runtime before the focused closed-selector fixture extension.
- Ruff, formatting, Pyright on both versions, the core dependency check, and the diff check passed.

- [GitHub story](https://github.com/mgd43b/plotroom/issues/89)
