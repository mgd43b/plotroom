# shellcheck shell=bash disable=SC2034
# Sourced by the clerk's scripts; never run on its own. It has no `set -e` (the scripts rely on unset variables and on greps that return 1).
# Everything is found from the directory you run the script in (a checkout or a worktree of the repository), never from where the script lives:
#   OR_SCRIPT_DIR  this folder (only used to find the templates)
#   OR_ROOT        the repo root: ORCH_ROOT, else `git rev-parse --show-toplevel` from the current directory. The scripts run from there.
#   OR_REPO        owner/name: ORCH_REPO, else `gh repo view`. Exported as GH_REPO so every gh command uses it. When ORCH_REPO is set, origin must be that repository.
#   OR_OWNER, OR_NAME  the two halves of OR_REPO
#   OR_BASE        the branch merges land on: ORCH_BASE_BRANCH, else the repository's default branch from gh
#   OR_CI_WORKFLOW the CI workflow whose runs decide whether the base branch is green: ORCH_CI_WORKFLOW (a workflow name or file), else every workflow
#   OR_MIN_CHECKS  how many checks a PR must have before it can merge: ORCH_MIN_CHECKS, REQUIRED (no default): the number of checks your CI always starts for a PR.
#                  A PR that reports fewer is not ready, so one that looks finished before its last job has registered is never merged. Too high and nothing merges;
#                  too low and the gap is open. init-state.sh sets OR_MIN_CHECKS_OPTIONAL=1 before sourcing this, as the one script that does not merge.
#   OR_STATE_DIR   per-run state (ledger.md, triggers.md, skip.txt, merges.log, sweep memory): ORCH_STATE_DIR, else
#                  ${XDG_STATE_HOME:-$HOME/.local/state}/agent-orchestration/<owner>-<repo>. It is outside git on purpose: it holds session ids.
OR_SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OR_ROOT=${ORCH_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}
[ -n "$OR_ROOT" ] || { echo "orch: cannot find the repo root (run from inside the checkout, or set ORCH_ROOT)" >&2; exit 1; }
cd "$OR_ROOT" || exit 1
OR_REPO=${ORCH_REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)}
case "$OR_REPO" in */*) ;; *) echo "orch: cannot find owner/name from gh (set ORCH_REPO=owner/name)" >&2; exit 1;; esac
# One repository for every read and write: gh takes it from GH_REPO in every command, and git fetches from origin, so when ORCH_REPO names a repository,
# origin must be that same one or nothing runs (the gate must not apply one repository's review result to another's PR).
export GH_REPO="$OR_REPO"
if [ -n "$ORCH_REPO" ]; then
  origin=$(git remote get-url origin 2>/dev/null)
  case "$origin" in
    *"$OR_REPO"|*"$OR_REPO".git) ;;
    *) echo "orch: ORCH_REPO is $OR_REPO but origin is ${origin:-unset}; they must be the same repository" >&2; exit 1;;
  esac
fi
OR_OWNER=${OR_REPO%%/*}
OR_NAME=${OR_REPO#*/}
OR_BASE=${ORCH_BASE_BRANCH:-$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name 2>/dev/null)}
[ -n "$OR_BASE" ] || { echo "orch: cannot find the default branch from gh (set ORCH_BASE_BRANCH)" >&2; exit 1; }
OR_CI_WORKFLOW=${ORCH_CI_WORKFLOW:-}
OR_MIN_CHECKS=${ORCH_MIN_CHECKS:-}
if [ -z "$OR_MIN_CHECKS" ] && [ -n "$OR_MIN_CHECKS_OPTIONAL" ]; then OR_MIN_CHECKS=1; fi
[ -n "$OR_MIN_CHECKS" ] || { echo "orch: set ORCH_MIN_CHECKS to the number of checks your CI always starts for a PR (a PR that reports fewer is not ready to merge)" >&2; exit 1; }
case "$OR_MIN_CHECKS" in ""|*[!0-9]*|0) echo "orch: ORCH_MIN_CHECKS must be a number of at least 1 (a PR with no checks cannot be gated)" >&2; exit 1;; esac
OR_STATE_DIR=${ORCH_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/agent-orchestration/${OR_OWNER}-${OR_NAME}}

# or_main_verdict: sets OR_MAIN_KIND (green | red | none | unreadable) and OR_MAIN_DETAIL ("<conclusion> <sha8>" or a reason) from the base branch's CI runs.
# The newest completed run that has a verdict decides. A run superseded mid-flight can end "failure" with every job canceled and none failed; that is no
# verdict (it says nothing about the branch), so the next older run is read. A run is red only when a job failed or timed out (or it never started).
# Runs that are canceled or skipped are never looked at. If a run's jobs cannot be read, the branch is not called green.
or_main_verdict() {
  OR_MAIN_KIND=none; OR_MAIN_DETAIL="no completed CI run with a verdict"
  local runs runid concl sha8 failed wf
  wf=(); [ -n "$OR_CI_WORKFLOW" ] && wf=(--workflow "$OR_CI_WORKFLOW")
  runs=$(gh run list --branch "$OR_BASE" ${wf[@]+"${wf[@]}"} --status completed --limit 30 --json databaseId,conclusion,headSha --jq '.[]|select(.conclusion!="cancelled" and .conclusion!="skipped")|"\(.databaseId) \(.conclusion) \(.headSha[0:8])"') || { OR_MAIN_KIND=unreadable; OR_MAIN_DETAIL="could not list the CI runs"; return; }
  while read -r runid concl sha8; do
    [ -n "$runid" ] || continue
    if [ "$concl" = success ]; then OR_MAIN_KIND=green; OR_MAIN_DETAIL="success $sha8"; return; fi
    if [ "$concl" = startup_failure ]; then OR_MAIN_KIND=red; OR_MAIN_DETAIL="$concl $sha8"; return; fi
    failed=$(gh run view "$runid" --json jobs --jq '[.jobs[]|select(.conclusion=="failure" or .conclusion=="timed_out")]|length')
    case "$failed" in ''|*[!0-9]*) OR_MAIN_KIND=unreadable; OR_MAIN_DETAIL="the jobs of run $runid could not be read ($sha8)"; return;; esac
    if [ "$failed" -gt 0 ]; then OR_MAIN_KIND=red; OR_MAIN_DETAIL="$concl $sha8"; return; fi
  done <<< "$runs"
}
