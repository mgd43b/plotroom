# Dedicated branch-update observation

The GitHub fixture branch-update workflow is for [#67](https://github.com/mgd43b/plotroom/issues/67). Its default preflight reads public evidence without a credential. It checks the dedicated repository, current main, immutable ancestor, README append, and empty open PR list.

## Approval for one acceptance run

Approve one first-attempt manual dispatch by the owner on the reviewed Plotroom main SHA, with phase acceptance and explicit write authorization. The target is exclusively the dedicated fixture repository. Use the reviewed fixture ancestor and base values below.

| Input | Value |
| --- | --- |
| Repository | mgd43b/plotroom-test |
| Ancestor | 86ecdf31c557eaa1bf619bfa3fca29d213d7c6ef |
| Main before and after | 9a6ce8a89a15046c18dbfa5d7d2af88bb0edbc14 |

Approval includes exclusive use of the run-owned ref and PR until the update and cleanup are resolved. GitHub's README edit checks the file blob, and ref deletion takes a branch name. The runner checks ownership and refs immediately before setup and cleanup writes. Those checks leave a race if another writer uses the disposable ref.

The write budget includes three setup requests, one update-branch request, and up to two cleanup requests. Setup creates a run-named branch at the ancestor, changes only the README heading, and opens one PR against fixture main. Its immutable head is recorded and checked before submission. The original fixture CI can run when the PR opens or synchronizes. Cleanup closes the verified unmerged PR and deletes its verified branch after a confirmed observation.

A fresh stale-CI refusal with matching projection pins and a journal entry for the same head and verdict reasons is required before submission. The runner consumes its attempt before calling GitHub. The observation process reopens the original local daemon state, witnesses the new head, and verifies both planned parents and the combined README edits. Cleanup additionally requires a recorded 202 and current ownership and ref checks.

## Outcome limits

GitHub enforces the expected PR-head SHA and returns 202 on acceptance. The endpoint exposes no expected-base SHA or caller-keyed receipt. Preflight and readback record observed refs and participating commits. Exact-base atomicity and provider idempotency remain unverified.

A timeout, missing response, unexpected state, or changed ref stops the experiment without another submission. Logs identify the run-owned fixture for operator reconciliation. A local restart can continue observation while the same job directory survives. The workflow's local journal has no recovery claim after loss of the hosted runner. A separately dispatched run requires a new approval.

Controller merge execution remains disabled. This workflow records a branch-update observation only, and the full #67 live acceptance stays open.
