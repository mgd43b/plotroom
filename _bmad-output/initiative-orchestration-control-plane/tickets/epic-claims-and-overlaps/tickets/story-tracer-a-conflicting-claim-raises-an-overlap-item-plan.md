---
title: 'Epic 5 local claims and overlaps'
type: feature
ticket: 1
created: '2026-11-23'
status: in-review
baseline_revision: 'e09371505fef2d84711108c044f7ed7bfe862236'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context: ['{project-root}/AGENTS.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The local board has no durable claims or advance notice of overlapping lane work and ticket moves.

**Approach:** Deliver Epic 5 as one PR, implementing #76 through #81 in prerequisite order. Reconcile state-backed next items from claims and existing GitHub projections in the project writer transaction. Expose local MCP commands and reads.

## Boundaries & Constraints

Use the existing role and lane bindings, writer, projections, next items, BMad ticket tree configuration, and deterministic fixtures. Reserve migration 0013 and use the current local head during development. The coordinator handles readiness and the clerk handles merging. No remote writes during implementation, new GitHub endpoints, hosted work, live fixtures, paid tests, or Epic 6 briefs.

## I/O & Edge-Case Matrix

| Given | When | Then |
|---|---|---|
| A worker claims `src/a/**` | The orchestrator claims `src/a/x.py` for lane B | One item names both lanes and clears on release or archive |
| Two lanes and a slashless `config.yml` claim | A lane claims `src/config.yml` | The conservative prefixes overlap. Cross-lane worker writes and negation are refused |
| A lane A PR touches files of A and B | File facts arrive before or after the claims | Only B's conflict opens. Unbound PRs compare against all claims. A changed or closed PR or released claim clears a resolved conflict |
| A configured ticket tree and two editing PRs | A third PR renames their story file | One item lists the editing PRs and suggests they merge first. An untouched move produces none |
| Current visible conflicts and claims | A caller uses PR and lane filters, paging, and snapshot | The read results match visible next items and include claim reasons. Snapshot counts stay complete across pages |

</frozen-after-approval>

## Code Map

- `core/catalog.py`, `core/lanes.py` and `core/unit_of_work.py` under `src/plotroom/` provide writer rules, lane lifecycle, stable item updates, and snooze behavior.
- `core/github.py` and `core/github_projection.py` write authoritative PR facts and head-scoped file resources. Use confirmed current-head rows for conflicts, including cached confirmed files after a same-head refresh failure. Add an uncapped open-PR query because the poller rotates eight targets. Ignore labels and work-item assignment for ownership. Infer a lane from the same-repository branch binding.
- `app/serve.py` resolves the project's selected source. Pass its repository-relative tree path to core for tree moves when applicable.
- `adapters/store_sqlite/`, `ports/store.py`, and `tests/fakes.py` implement the same writer and read contracts. Keep board counts in one committed read. Follow existing item and lane visibility and MCP budget conventions.

## Tasks & Acceptance

**Execution:**

- [ ] #76 Add `src/plotroom/core/claims.py` and migration `0013` on current local `0011`; normalize nonnegated gitignore-style paths, persist reason and stable identity, reconcile one item per unordered lane pair, and clear claims on archive. Reuse `owns_lane`. Make no-op updates stable. Cover normalization, path boundaries and ownership in focused tests.
- [ ] #77 Reconcile one PR/claiming-lane item in the transaction on claim mutations, PR state/head, complete file facts, and changed lane bindings. Include relevant rename paths. Reuse open pull targets and deterministic stable item context. No labels required.
- [ ] #78 Reconcile one item per moving PR, aggregate relevant exact paths and distinct editing PR numbers; put editing PRs first in the suggested order. Ignore moves outside the configured tree and clear when the conflict ends.
- [ ] #79 Expose `claim`, `release`, `claims`, and `overlaps` in `src/plotroom/adapters/mcp/server.py`. Claims follow lane visibility. Overlaps preserve next-item visibility, filter to exactly one lane or PR before paging, and identify involved lanes and PRs. Snapshot counts use the same visible projections. Update exact transport assertions as needed.
- [ ] #80 Review deferred findings from the Epic 5 plans and review; patch in-scope findings and report genuine future work for ticketer routing. Do not add speculative changes.
- [ ] #81 Add `tests/test_claims_e2e.py` using the daemon and in-process fixture repository API, one test per Epic 5 Done-when line. Add focused tests for input, stale observations, lifecycle, deduplication, and populated migration.

**Acceptance Criteria:**

- Given overlapping claims or a conflicting projection, when the writer commits, then the orchestrator's next board shows the conflict before review and clears it when its facts change.
- Given a prior SQLite database and the current migration, when the daemon opens the upgraded store, then the existing rows remain and the revision has one head.
- Given the local fixture suite, when it runs through authenticated MCP on a real daemon, then all five Epic 5 Done-when checks pass independently.

## Implementation Notes

Migration 0013 now follows verified main's trigger migration 0012. The populated predecessor test opens the daemon twice, preserving its original events and the derived overlap item.

Claims and conflicts are distinct reads. Claims use lane visibility. Overlaps use next-item routing and paging, with full detail available by item ID.

## Plan Change Log

## Review Triage Log

- Medium, patched. Oversized claim rows retain a continuation offset and can be read in full by offset with the same visibility checks. The daemon test exercises both a long glob and a long reason.
- Low, documented. Truncated overlap pages have the same behavior as `next()`. Use the item's ID in `next(item=...)` to fetch its complete lanes and PRs.
- Medium, patched. A second rename is excluded from the editing PR recommendation to avoid reciprocal merge orders. Both paths still participate in claim matching. The ticketer received the separate move-against-move case for scope review.
- Low, referred to the ticketer. The local filesystem ticket-tree source has no repository-root binding. GitHub ticket-tree moves use the existing repository-relative source. Mapping a local tree to the polled repository requires a separate contract.
- Medium, patched. Git-style filename matching uses bytes and recognizes escaped separators and closing brackets in character classes. Focused fixtures cover the matched and rejected forms.
- High, patched. The legacy trigger fixture seeds facts on its pre-trigger schema before current reconciliation is enabled. The transport matrix supplies required claim and release arguments for drain authorization checks.
- Medium, patched. Daemon startup clears persisted PR-derived items if GitHub has been disabled. The populated upgrade test restarts in both configurations.
- Medium, patched. Claim-pair evidence stores each participating claim once, keeping the item size proportional to the number of claims.

## Verification

- `uv run python -m pytest -q tests/test_claims_e2e.py tests/test_migrations.py tests/test_transport_contract.py`
- Use the lint, type, and full test commands defined in `.github/workflows/ci.yml`.
