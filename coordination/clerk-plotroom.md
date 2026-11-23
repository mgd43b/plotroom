# Plotroom clerk pilot

[The pilot clerk goal](goals/clerk-plotroom.txt) is for an owner-approved working day. [Issue #110](https://github.com/mgd43b/plotroom/issues/110) remains open pending that day and its evidence.

## Offline rehearsal

From the repository root, run the fixture rehearsal.

```sh
uv run --locked python -m pytest -q tests/test_clerk_pilot.py
```

The test creates a temporary local daemon and fixture credentials, and imports [the sample triggers](../tests/fixtures/clerk_import/pilot-triggers.md) and [ledger](../tests/fixtures/clerk_import/pilot-ledger.md) through `plotroom import` with its orchestrator token. It starts `plotroom watch` as clerk from the board cursor, then makes PR #401 eligible using fixture provider facts. The wake identifies the MERGE offer. The test calls `merge_gate` with the offer's two SHA pins and passes its command to an in-memory stand-in. It observes the resulting merge receipt and a single trigger fire after repeated polling. A separate fixture PR without an open offer produces an `outside_gate` receipt and an orchestrator item.

The stand-in checks both refs at its in-memory write boundary. The optional controller merge and branch-update write adapters also require injected fixtures. The rehearsal uses neither a real GitHub write nor a drain session. Plotroom's manual command is exercised through the stand-in, with the controller's automated writes off.

## Owner prerequisites

- Review the live ledger fields against the imported template contract using [#12](https://github.com/mgd43b/plotroom/issues/12). Account for any extra fields or unknown rows before approving the pilot. Keep the ledger and triggers private. Check prior fired markers, exact bindings, and literal placeholders in the source. Use the lane checklists for the catch-all post-merge steps.
- Confirm the real drain delivery evidence in [#60](https://github.com/mgd43b/plotroom/issues/60), and decide how to handle stale CI and the remaining real branch-update evidence in [#67](https://github.com/mgd43b/plotroom/issues/67). Name and configure the drain session. After import reconciliation, run it through the day with its own drain token, using `wait`, `outbox_lease` and `outbox_done` after native delivery with the action key.
- Verify the local daemon, the GitHub projection, required checks, configured merge method, worker routes and separate orchestrator, clerk and drain access. Keep `controller_merges = false`. Have the coordinator reconcile every imported skip and hold with the pilot exclusions and gate configuration, including the configured skip label where applicable. The importer raises items for these decisions.
- Confirm the working-day window, authorized clerk, and return to the existing process if the pilot stops. The owner pauses the existing sweep and confirms it is idle before taking the final private copies and importing them. Reconcile any merges and messages that occurred before the pause to avoid duplicate sends.
- Run the import as orchestrator against the already-running private daemon, with both files or with triggers first. Review open triggers against current projections and configured delivery before submitting. An already-true open trigger can queue an action during import, and an enabled relay can deliver it immediately. Keep the drain stopped until the coordinator has compared the report, trigger states, lanes, checklists, unresolved items and any queued effects with the copies.

```sh
plotroom import --config "$pilot_config" --token-id "$orchestrator_token_id" \
  --triggers "$copied_triggers" --ledger "$copied_ledger"
```

## Clerk execution and acceptance

At startup and after a wake, page through `next` for current items. Use the first page’s cursor for the next watch after handling those offers. Take a current `merge_ready` item assigned to the clerk and call `merge_gate` with its `context.merge_gate` arguments immediately before the merge. Require a `MERGE` verdict for the item's `expected_head_sha` and `expected_main_sha`. Execute the item's `context.command` once with the configured GitHub access. The printed `gh pr merge` command uses `--match-head-commit` to enforce the PR head. The main SHA is checked during preflight and can move between the recheck and the GitHub write. The owner must approve the manual path with that limitation before the day starts.

Stop on a refusal, changed pin, uncertain result, or missing projection. If a write result is uncertain, reconcile GitHub and the observed merge before any further attempt. A refusal or orchestrator-owned item goes to the coordinator for a decision. A wake or an old item alone cannot authorize a merge.

Record the working-day start and end, sweep pause and any sweep invocation, clerk item IDs with both pins, command outcomes, observed merges, and each imported trigger's condition and fire count. Compare actual GitHub merge history with the clerk's execution log. An open offer for the same head is enough for Plotroom to classify an observation as `gated`, so the classification cannot by itself identify the actor. Confirm that merges without matching offers produce `outside_gate` items visible to the orchestrator.

The owner accepts the day when every clerk merge followed a current MERGE offer, all eligible imported triggers fired once, and the record shows no sweep run or missed or duplicate merge. Retirement, refactoring and the epic acceptance suite follow the owner's decision in [#111](https://github.com/mgd43b/plotroom/issues/111), [#112](https://github.com/mgd43b/plotroom/issues/112) and [#113](https://github.com/mgd43b/plotroom/issues/113).
