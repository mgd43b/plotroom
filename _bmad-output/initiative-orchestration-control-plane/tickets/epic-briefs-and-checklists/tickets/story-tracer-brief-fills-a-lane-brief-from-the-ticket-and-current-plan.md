---
title: 'Build a lane brief from current project facts'
type: feature
ticket: 1
created: '2026-11-23'
status: built
baseline_revision: '65ed0835eeab89276f197f375cc1ebdcd87dfc54'
route: full
route_source: auto
review: thorough
review_source: pinned
lenses_ran: [acceptance, lifecycle, integration]
risk: medium
context: [AGENTS.md]
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Give the orchestrator a typed `brief(work_item, worker)` MCP read that fills a proposed lane brief from a ticket, current claims, and project settings.

## Boundaries & Constraints

Use the configured read-only work-item source. Return the full source text, requested worker's current lane claims, other lanes' current claims, the plan and RESULT shapes, and configured standing rules. Leave sending to the orchestrator. Preserve project isolation and use the central role table.

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Before start | Mirrored ticket and configured worker without a lane | Full ticket text, an empty own-paths section, current don't-touch claims, plan and RESULT, and rules | Unknown worker or ticket yields a typed error |
| After start | Worker with a lane and claims | Own claims appear in paths and other lanes' claims appear under don't-touch | Released claims disappear from the next read |
| Other role | Worker requests a brief | Typed forbidden result | Source is never consulted |

</frozen-after-approval>

## Code Map

- `ports/work_items.py`, `adapters/work_items_bmad.py`, `adapters/github/ticket_tree.py`, and `adapters/work_items_github_issues.py` define the three supported sources. Add typed per-reference full text lookup via the trusted BMad reader's `find` operation under the configured tree or the matching projected issue body. Reuse remote tree materialization and freshness safeguards. Keep lookup outside the writer. Validate local selected files stay inside the ticket scope and bound reads.
- `app/config.py` and `app/serve.py` own project settings and source binding. Add optional project brief settings with a safe default and validated named template fields for source text, work item, worker, paths, don't-touch, plan, RESULT, and standing rules. Use readable single-pass interpolation and document settings in `README.md`.
- `core/catalog.py`, `core/ledger.py`, and `adapters/mcp/server.py` own role checks and MCP. Authorize the read before accessing the source. Read the worker's lane and all current claims together from the store or as a read-only catalog action. Return a structured envelope containing the brief string. Do not use the command event log for the read's text.
- `tests/test_transport_contract.py` has the current every-tool transport evidence. Keep it complete and reuse the daemon fixtures for exact contents at the tool boundary.

## Tasks & Acceptance

- [x] Add and exercise per-reference body reads for supported work-item sources.
- [x] Add project-configured rendering and expose the read through the normal MCP entry point.
- [x] Verify the typed tool and worker denial on a mirrored ticket with two other claimed lanes and configured standing rules. Test before and after own claims and after a release.
- Given the local fixture and a worker, when the orchestrator reads a brief, then the response matches the complete expected text including the unchanged ticket body and live claims.
- Given a worker token, when it calls the tool, then the role check denies it without touching the source.

## Implementation Notes

The source lookup runs outside the writer. The brief reads the worker's current lane and active claims in one committed store snapshot, and applies the project template without reinterpreting ticket text. The source and transport fixture suite passed 89 tests.

## Plan Change Log

## Review Triage Log

- Medium, patch. Acceptance and integration each identified compact ready tickets that produced an absent body. The reader now returns the text fields from the matching in-scope entry. A mirror-to-brief fixture verifies the same reference through both paths.
- Medium, patch. Acceptance and integration each identified that body lookup bypassed the source's configured issue scope. Lookup now shares ticket and effective milestone membership with the mirror. Focused fixtures cover explicit and inherited assignments and an in-progress ticket.
- Medium, patch. The integration recheck found that the compact renderer could still return earlier subprocess text after validating a different file read. The trusted lookup now locates the ticket, and the confined TOML read provides the rendered text.
- Medium, patch. Opening Kodus found repeated remote briefs rematerialized an unchanged ticket tree. A bounded cache keyed by the resolved commit and ref avoids repeated blob downloads. The fixture verifies cache reuse, commit changes, and recovery from a failed read.

## Verification

- `rtk proxy uv run --locked python -m pytest tests/test_transport_contract.py tests/test_work_items.py tests/test_work_items_github_issues.py tests/test_work_items_github_tree.py tests/test_config.py`
- [Built in PR #159](https://github.com/mgd43b/plotroom/pull/159)
