---
title: Verify clerk ledger import against observed field shapes
type: bugfix
ticket: 7
created: '2026-11-25'
status: built
baseline_revision: '8b726540cc92b07fae40c59975b6959539990bb8'
route: oneshot
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
---

<frozen-after-approval reason="human-owned intent">

## Intent

An empty board in the hand-run clerk's ledger can contain a display-only row that the importer currently rejects. Accept that form only when the Open PRs table unambiguously contains zero PRs. Preserve numbered PR and supported pending rows. Refuse ambiguous or malformed empty markers before submission.

Classify the observed ledger structurally and verify the importer with synthetic data in a disposable daemon. The first import must produce the expected lanes, after-merge work and advisories. The second must leave durable state unchanged.

</frozen-after-approval>

## Implementation Notes

- The compatibility check belongs at the Markdown boundary, after header and row-width validation and before typed rows reach the writer.
- The local evidence concerns a single display row with five blank detail cells. Repeated Open PRs sections and mixed markers are ambiguous. An independent Skip list remains eligible for its own advisories.
- The repository title validates repository identity. The Open PRs table supplies numbered PRs, owner, channel, state and After merge. Kind is descriptive, and the pending PR marker yields no lane. Merge history, lanes without open PRs, coordination inventory and free-form notes stay in the source. The snapshot had no Skip list, outside-gate table or flake receipt table.
- The populated cases use independently generated fixture data. Live values and connection details are outside this record.

- Redacted observation. The canonical Open PRs columns contained one display marker and zero numbered rows. A four-column merge-history table, a four-column pending-lane table, a three-column coordination inventory and operational prose were present. Their fields and records remain in the hand-run source under the import contract.
- Verified the observed shape with the local parser at the pinned baseline plus this change. It returned zero Open PRs and zero Skip list rows. The disposition covers the frozen ledger's observed fields. Populated behavior is evaluated with synthetic data.
- Two disposable CLI scenarios passed. The empty shape yielded zero lanes, triggers or advisories. The populated shape yielded two projected and channel-preserving lanes, an exact trigger link, a checklist row, and separate unknown-worker, state-hold and skip advisories. Both second imports returned identical IDs and left the board cursor, trigger inventory and persisted table contents unchanged. The action queue was empty.

## Plan Change Log

## Review Triage Log

- Independent quick review found no actionable issues in the parser or regressions. Thirteen supplemental synthetic parser checks covered standalone, malformed, repeated and mixed markers and existing numbered and pending forms.

## Verification

- Given the verified table shape, when the importer reads the empty marker, then it produces zero typed PRs. A separate valid skip row still yields a skip advisory.
- Given a populated, repeated, or annotated marker, when the importer parses the table, then it refuses before submission. Existing numbered and pending fixture rows retain their outcomes.
- Recheck the frozen ledger with the parser locally and retain only the supported shape and aggregate result.
- Run `uv run --locked python -m pytest -q tests/test_clerk_import.py tests/test_clerk_pilot.py`. Compare results of two synthetic CLI imports and persisted state in a disposable daemon.
- The focused importer/pilot suite and disposable CLI re-check passed together, 33 tests.
