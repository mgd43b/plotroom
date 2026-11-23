---
title: 'Import clerk triggers'
type: feature
ticket: 1
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

An orchestrator can import the clerk trigger template locally through the running server. Exact PR and branch predicates use ordinary once triggers. Imported FIRED entries carry fired state without creating effects. Unrecognized entries open bind items. Bracketed message fields stay literal and are noted on the trigger. The same import makes no new records or effects.

</frozen-after-approval>

## Code Map

- Core implementer owns `core/clerk_import.py`, trigger/lane/checklist core and SQL integrations, the catalog, and any migration. The parent owns Markdown parsing, CLI, MCP registration and adapter tests.
- `core/clerk_import.py` exports TypedDicts named `TriggerImportRow` (label: str, source: str, predicate: dict[str, Any] | None, text: str | None, channel: str | None, notify: bool, fired: bool), `PullImportRow` (pr: int, worker: str, channel: str, after_merge: str), and `SkipImportRow` (pr: int, reason: str).
- The registered `clerk_import` command takes required arrays `triggers`, `pulls`, and `skips`. Return JSON with `triggers`, `lanes`, `items` mapping source labels/PRs to IDs, as appropriate. Process triggers before lanes; preserve typed source row values as text.
- `core/triggers.py` is the once-trigger engine. `core/trigger_actions.py` already validates configured send targets. `UnitOfWork.enqueue_action` distinguishes the two allowed action kinds for a worker message with companion coordinator notice.
- `core/ledger.py` deduplicates commands by request ID. MCP derives the batch request ID from canonical typed arguments. Use immutable content/source receipts or equivalent durable per-entry journaling to deduplicate overlapping input sets and avoid reviving previously acknowledged items. Keep trigger label identities stable for later ledger-only import and preserve conflicts for human review.

## Tasks & Acceptance

- [x] Register `clerk_import` for orchestrator only, and add optional Boolean `imported_fired` to the orchestrator's `trigger` command. Reject fired recurring triggers. Use created sequence as the import record's fired sequence without synthetic fire or send.
- [x] Support known exact predicates only for imported trigger rows. Map a channel to a unique configured delivery route. Create a bind item for unmatched/ambiguous routes and unsupported conditions/actions. When both forms appear, enqueue a companion notification on the same real fire. Persist/render a placeholder note when message text includes `[sha8]` or `[N]`.
- [x] Read the sibling ledger plan and implement its core tasks in the same handoff. Keep the Alembic change additive with nullable metadata, revision 0019 if needed. Use baseline 0017 locally, then the parent will integrate actual main once and update the unmerged parent if needed.
- [x] Given open, fired, and prose fixtures, importing then replaying creates two trigger records and one bind item. The fired row has no effects. A later matching observation creates one ordinary fire, with its static text.

## Implementation Notes

- A private Unix listener request runs the orchestrator command. The CLI uses a stored local token and requires an existing verified daemon. Stable batch and row receipts preserve replay across overlapping files.
- Imported notifications use static text. An exact worker message with a companion coordinator notice queues one of each from the same ordinary fire. Historical fires allocate no effect.

## Plan Change Log

## Review Triage Log

- Medium, edge cases. HTML comments hid a trigger from the reader but left it active in the parser. Commented sections are excluded.
- Medium, verification. A missing send or coordinator notice went undetected. The fixture now verifies both durable effects, their common cause, and literal text.
- Low, verification. The acknowledgment test stopped at the batch receipt. A different batch now replays the bind row and verifies it stays acknowledged.
- Low, verification. The standalone imported Boolean was only tested against denied roles. Both protocol eras now exercise local once and every requests and a TCP refusal.

- Follow-up review verified code-block indentation and literal quoted comment text.

## Verification

- `uv run --locked python -m pytest -q tests/test_clerk_import.py` for import, auth and replay.
- Run Ruff and Pyright for changes and the existing trigger suite.
