#!/bin/bash
# safemerge.sh <pr> [--allow-overlap]
# The merge gate. Merges a PR only if: it has at least ORCH_MIN_CHECKS checks, all finished with no failure, there are no unresolved review threads, the base
# branch's latest CI run with a verdict is green (or this PR is the marked fix), no closing keyword would close an issue before it is verified, and no file the PR
# changes was also changed on the base branch since the PR last merged it (the stale-CI rule: update the branch, run CI again). Merges pinned to the head
# commit that was checked. It fails closed: anything it cannot read is a refusal, never a pass. Run it from inside the checkout (see lib.sh).
#
# Exit codes: 0 merged (or a dry run), 1 could not read the PR or the merge failed, 2 refused (checks, threads, a head that moved, a base branch with no verdict,
# or a git/gh read that failed), 3 refused (overlap with the base branch), 4 refused (closing keywords), 5 refused (the base branch is red).
# What it cannot see: only the files both sides changed. A fixture, bound or contract that the base branch changed in another file is for the lane and the
# coordinator to judge; update the branch when in doubt.
# Environment (all optional; ORCH_ROOT, ORCH_REPO, ORCH_BASE_BRANCH, ORCH_CI_WORKFLOW, ORCH_MIN_CHECKS and ORCH_STATE_DIR are in lib.sh):
#   ORCH_MIN_CHECKS        required: the number of checks CI always starts for a PR; a PR that reports fewer is refused (see lib.sh)
#   ORCH_IGNORE_CHECKS     check names, separated by ";", whose failure is not the PR's (a review bot that is down). Exact names; a name with ";" is not supported.
#   ORCH_MAINFIX_OK=1      lets the one PR that turns a red base branch green merge
#   ORCH_KEYWORDS_OK=1     allows closing keywords
#   ORCH_DRY=1             stops before the merge and prints what it would do
#   ORCH_EXPECT_HEAD=<sha or prefix>   refuses (exit 2) when the PR's head is not that commit (mergewait.sh sets it, so a push during its wait is not merged unseen)
#   ORCH_MERGE_METHOD      merge (default), squash or rebase
# shellcheck source=/dev/null
. "$(dirname "$0")/lib.sh"
n=$1; allow=$2
[ -n "$n" ] || { echo "usage: safemerge.sh <pr> [--allow-overlap]" >&2; exit 1; }
case "$n" in *[!0-9]*) echo "usage: safemerge.sh <pr number>" >&2; exit 1;; esac
method=${ORCH_MERGE_METHOD:-merge}
case "$method" in merge|squash|rebase) ;; *) echo "orch: ORCH_MERGE_METHOD must be merge, squash or rebase" >&2; exit 1;; esac
git fetch -q origin || { echo "REFUSE #$n: could not fetch origin (a stale origin/$OR_BASE would hide an overlap)"; exit 2; }
head=$(gh pr view "$n" --json headRefOid --jq .headRefOid) || exit 1
[ -n "$head" ] || exit 1
case "$head" in "$ORCH_EXPECT_HEAD"*) ;; *) echo "REFUSE #$n: the head moved to ${head:0:8}; it was ${ORCH_EXPECT_HEAD:0:8} when the wait began"; exit 2;; esac
git fetch -q origin "pull/$n/head" || exit 1
# One snapshot of the checks: the first line is the pending count, the second the total, the rest the names of the failing ones.
snap=$(gh pr checks "$n" --json name,bucket --jq '([.[]|select(.bucket=="pending")]|length), length, (.[]|select(.bucket=="fail")|.name)')
pending=$(printf '%s\n' "$snap" | sed -n 1p)
total=$(printf '%s\n' "$snap" | sed -n 2p)
case "$pending" in ''|*[!0-9]*) echo "REFUSE #$n: could not read the checks"; exit 2;; esac
case "$total" in ''|*[!0-9]*) echo "REFUSE #$n: could not read the checks"; exit 2;; esac
failnames=$(printf '%s\n' "$snap" | sed 1,2d)
if [ -n "$ORCH_IGNORE_CHECKS" ]; then
  failnames=$(printf '%s\n' "$failnames" | grep -vxFf <(printf '%s\n' "$ORCH_IGNORE_CHECKS" | tr ';' '\n'))
fi
fails=$(printf '%s\n' "$failnames" | awk 'NF { s = s (n++ ? "; " : "") $0 } END { print s }')
[ "$pending" = "0" ] || { echo "REFUSE #$n: $pending checks pending"; exit 2; }
[ "$total" -ge "$OR_MIN_CHECKS" ] || { echo "REFUSE #$n: only $total checks have reported (at least $OR_MIN_CHECKS expected)"; exit 2; }
[ -z "$fails" ] || { echo "REFUSE #$n: failing: $fails"; exit 2; }
q="query{repository(owner:\"$OR_OWNER\",name:\"$OR_NAME\"){pullRequest(number:$n){reviewThreads(first:100){totalCount nodes{isResolved}}}}}"
u=$(gh api graphql -f query="$q" --jq 'if .data.repository.pullRequest.reviewThreads.totalCount > 100 then "many" else ([.data.repository.pullRequest.reviewThreads.nodes[]|select(.isResolved==false)]|length) end')
case "$u" in
  0) ;;
  many) echo "REFUSE #$n: more than 100 review threads; the gate cannot count them all"; exit 2;;
  ''|*[!0-9]*) echo "REFUSE #$n: could not read the review threads"; exit 2;;
  *) echo "REFUSE #$n: $u unresolved threads"; exit 2;;
esac
or_main_verdict
case "$OR_MAIN_KIND" in
  green) ;;
  red) if [ -n "$ORCH_MAINFIX_OK" ]; then echo "NOTE #$n: $OR_BASE is red ($OR_MAIN_DETAIL); merging as the fix (ORCH_MAINFIX_OK)"; else echo "REFUSE #$n: $OR_BASE's latest CI verdict is red: $OR_MAIN_DETAIL (only the fix merges; ORCH_MAINFIX_OK=1 for that one)"; exit 5; fi;;
  *) echo "REFUSE #$n: $OR_BASE has no usable CI verdict: $OR_MAIN_DETAIL"; exit 2;;
esac
# Each command is checked: a missing merge base or a failed diff must not look like "no overlap".
base=$(git merge-base "$head" "origin/$OR_BASE") && [ -n "$base" ] || { echo "REFUSE #$n: no merge base with origin/$OR_BASE"; exit 2; }
prfiles=$(git diff --name-only "$base" "$head") || { echo "REFUSE #$n: could not list the files the PR changes"; exit 2; }
basefiles=$(git diff --name-only "$base" "origin/$OR_BASE") || { echo "REFUSE #$n: could not list the files $OR_BASE changed"; exit 2; }
overlap=$(comm -12 <(printf '%s\n' "$prfiles" | sort) <(printf '%s\n' "$basefiles" | sort))
if [ -n "$overlap" ] && [ "$allow" != "--allow-overlap" ]; then
  echo "REFUSE #$n: $OR_BASE changed these PR files since the PR's base ${base:0:8} (stale CI: update the branch and run CI again):"; printf '%s\n' "$overlap" | sed 's/^/  /'; exit 3
fi
kw=$( { gh pr view "$n" --json body --jq .body; git log --format=%B "$base..$head"; } | grep -oiE '\b(close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved) #[0-9]+' | sort -u | tr '\n' ' ')
[ -n "$kw" ] && [ -z "$ORCH_KEYWORDS_OK" ] && { echo "REFUSE #$n: closing keywords would close issues before they are verified: $kw (edit the body or commits to say Refs, or ORCH_KEYWORDS_OK=1)"; exit 4; }
[ -n "$ORCH_DRY" ] && { echo "DRY #$n: would merge ${head:0:8}"; exit 0; }
gh pr merge "$n" "--$method" --match-head-commit "$head" || exit 1
# Merged. Report the PR's own merge commit (not the base's tip, which a later merge may have moved); a failed lookup or fetch must not hide the merge.
git fetch -q origin 2>/dev/null
sha=$(gh pr view "$n" --json mergeCommit --jq .mergeCommit.oid 2>/dev/null)
mkdir -p "$OR_STATE_DIR" 2>/dev/null && echo "$(date -u +%Y-%m-%dT%H:%MZ) $n ${sha:0:8}" >> "$OR_STATE_DIR/merges.log" 2>/dev/null
if [ -n "$sha" ]; then echo "MERGED #$n -> ${sha:0:8} $(git log --format=%s -1 "$sha" 2>/dev/null)"; else echo "MERGED #$n -> (merge commit not read)"; fi
exit 0
