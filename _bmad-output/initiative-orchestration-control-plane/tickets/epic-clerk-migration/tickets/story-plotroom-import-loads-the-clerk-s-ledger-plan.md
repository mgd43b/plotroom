---
title: 'Import the clerk ledger'
type: feature
ticket: 2
created: '2026-11-23'
status: built
baseline_revision: '0b0e75fc4c0242ce8202dcc3164dfa0179d9ef0a'
route: full
route_source: auto
review: thorough
review_source: pinned
lenses_ran: [acceptance, edge_cases, verification]
risk: medium
context:
  - '{project-root}/AGENTS.md'
---

<frozen-after-approval reason="human-owned intent">

## Intent

An orchestrator can bring supported clerk ledger rows into the same local import. Workers come from configuration and branch/head facts come from the GitHub projection. Unconfigured workers and skipped PRs each get an orchestrator item. Preserve unfamiliar data conservatively. Real ledger schema evidence remains on #12.

</frozen-after-approval>

## Code Map

- Core implementer owns implementation under the sibling trigger plan's interface.
- `core/lanes.py` owns lane start and projected PR/checklist lifecycle. `core/checklists.py` owns checked steps, persistence and activation. Existing stores can hold nullable lane import metadata and explicit trigger ID links via a single additive migration, if needed.

## Tasks & Acceptance

- [x] Through lane start, import only the matching complete open same-repository PR with a configured worker and an authoritative projected head branch. Persist the literal ledger channel as metadata, with actual dispatch governed by configured routes.
- [x] Register orchestrator-only `checklist_import(lane, rows)` using the typed step/owner/ref rows and current projected PR/head. Explicit exact after-merge references to a previously imported trigger link to its ID rather than create duplicate checklist work. Preserve unresolved refs as checklist text.
- [x] Produce one item per skipped PR and per open PR with an unknown worker. Preserve missing or ambiguous authoritative PR/worker facts as actionable review. Ignore past flake receipts. Never touch GitHub labels.
- [x] Given the fixture import and replay, verify lanes, branch/channel, text checklist, exact trigger link and the expected items. Existing records remain intact on upgrade.

## Implementation Notes

- [Built in PR #163](https://github.com/mgd43b/plotroom/pull/163).
- The parsed ledger declares its repository. The writer checks that declaration before applying any part of the batch. Lane metadata records the imported channel and optional exact trigger ID. Unresolved references remain in the checklist.
- Nullable metadata in 0019 preserves prior lane and trigger rows. The branch and head still come from the selected project’s projection.

## Plan Change Log

## Review Triage Log

- Medium, acceptance. The header was ignored and could target the wrong repository. The combined batch now refuses a mismatch before any effects.
- Medium, acceptance. A colliding label could link a historical trigger. A durable ambiguity receipt keeps that label out of new links.
- Medium, acceptance. A changed hold or unresolved owner left stale context on its original item. The same advisory now changes when its facts change.
- Medium, edge cases. A fenced ledger example could be interpreted as table rows. The table must start at the first visible content in its section.
- Medium, edge cases and verification. Both other reviewers confirmed stale PR advisory context. The fixture verifies a changed hold wakes the existing advisory with the new details.

- Follow-up review verified first-source reservation for bind and exact outcomes.

- Medium, opening Kodus review. The importer discarded the State cell. It now preserves that text and shows an explicit state-only hold using the same per-PR advisory as the skip list.

- False, opening Kodus review. A trigger-linked lane gets its current PR and head from the GitHub projection on board reads. A head-movement assertion now covers this path without introducing another source of PR identity.
- Medium, opening Kodus review. Starting lanes repeated the idle-worker scan in a single batch. The importer refreshes idle items once after creating lanes.

- Low, CodeRabbit review. An explicit hold with an em dash separator now creates the same advisory, covered alongside the template wording.
- Medium, CodeRabbit review. An exact-branch check missed an existing lane pattern. The importer uses the gate’s matcher and preserves the existing binding.
- False, CodeRabbit review. A historical hold row in an overlapping batch is a replay with no ordering information. The later known advisory stays current, verified with a second unrelated hold in the old row’s batch.
- Medium, CodeRabbit review. Importing checklist work cleared an outstanding worker wait. Checklist import now preserves that wait and its existing item while pinning the post-merge work.

## Verification

- `uv run --locked python -m pytest -q tests/test_clerk_import.py tests/test_migrations.py`
- Check the populated prior revision upgrades to one head and preserves prior records.
- On the reviewed opening tree, 841 local Python 3.14 tests passed. The 40 affected tests passed on Python 3.14 and 3.15.
- After review changes, 47 affected tests passed on both Python versions. The hold-state round trip preserves existing lane IDs and explicit skips take precedence.
