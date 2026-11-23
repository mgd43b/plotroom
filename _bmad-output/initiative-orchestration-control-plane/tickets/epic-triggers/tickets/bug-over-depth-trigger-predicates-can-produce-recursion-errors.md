---
id: 12
type: bug
title: "Over-depth trigger predicates can produce recursion errors"
parent: epic-triggers
covers: [R1, R2, R3]
after: []
refined: false
hitl: false
risk: medium
tracker_id: "169"
remote: "https://github.com/mgd43b/plotroom/issues/169"
tracker_status: backlog
---

# Over-depth trigger predicates can produce recursion errors

## Description

An existing trigger test failed on Python 3.14 because recursion errors escaped through finalization while checking over-depth input. Trigger callers should receive the existing invalid-depth result deterministically.

## Observed verification failure

PR #168 head `0c7ddcee` failed Python 3.14 in `tests/test_triggers.py::test_excessive_predicate_nesting_returns_invalid`. Pytest reported an exception group with six unraisable recursion warnings. Python 3.15 passed at the same head. Both Python jobs passed at the opening head `129480e2`. The regression test was already present in PR #148. [Failed CI job](https://github.com/mgd43b/plotroom/actions/runs/45251976111/job/137963719961), [opening CI](https://github.com/mgd43b/plotroom/actions/runs/45250640391), [existing test at the #148 merge](https://github.com/mgd43b/plotroom/blob/331a79b360c289137c42e71ee5ccd2890cd89c72/tests/test_triggers.py#L271).

A subsequent local check using managed Python 3.14 reproduced the same class of unraisable recursion on pinned main `190b09d8ead462f60698343285594cc1a5243f72` and search head `0c7ddcee97256318b7896818f901d06398317741`. Both calls returned the intended invalid result and performed no write. The original CI log leaves the identities of the six finalized objects unconfirmed.

## Cause hypothesis

The initial argument snapshot in shared command execution may reach the interpreter recursion limit before the trigger predicate depth guard runs. The source establishes the order of the snapshot and later validation. The corrected test should establish whether that order caused the reported finalizer errors.

## Acceptance criteria

1. Valid predicates and existing validation results behave as before. Over-depth predicates deterministically return the existing invalid-depth result before incidental recursion or finalizer errors occur.
2. Command argument snapshots retain their immutability guarantees. A valid replay returns its existing receipt without duplicating trigger creation or effects. A rejected command leaves no partial trigger state or receipt.
3. A deterministic regression covers the rejection with normal warning handling. The fix head passes both Python 3.14 and 3.15 CI jobs on the initial attempt, with unchanged warning handling and recursion settings.

## References

- [Epic 3](https://github.com/mgd43b/plotroom/issues/3) and [composition and predicate validation #55](https://github.com/mgd43b/plotroom/issues/55).
- [Command snapshot on current verified main](https://github.com/mgd43b/plotroom/blob/190b09d8ead462f60698343285594cc1a5243f72/src/plotroom/core/ledger.py#L276) and [predicate depth guard](https://github.com/mgd43b/plotroom/blob/190b09d8ead462f60698343285594cc1a5243f72/src/plotroom/core/triggers.py#L134).
