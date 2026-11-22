#!/bin/bash
# sweep.sh [minutes | --once]
# One merge sweep. Every ORCH_SWEEP_INTERVAL seconds (default 120), for every open non-draft PR that is not on the skip list, whose checks have all started
# (at least ORCH_MIN_CHECKS, which is required: see lib.sh) and none is pending, it runs safemerge.sh. A stale-CI refusal (exit 3) updates the branch (GitHub merges the base branch in; never a
# force-push), which starts CI again. Prints one line per change, and a refusal's first line once per PR and reason. Stops after the minutes given (default 25),
# or after one pass with --once. Run it from inside the checkout (see lib.sh for the settings every script shares).
#
# What it keeps in the state folder: skip.txt (PR numbers to leave alone, one per line, anything after the number is a note; the coordinator or the human writes it),
# sweep.open (the PRs open at the last pass, so a PR that disappears is noticed), sweep.main-red (the red verdict already announced), merges.log (every gated merge).
# What it prints, and when:
#   MERGED #n -> sha subject            this pass merged it
#   #n branch updated (...)             stale CI; the branch was updated, CI runs again
#   #n needs its lane: ...              the branch update failed (a conflict); the lane must resolve it
#   REFUSE ...                          the gate's first line, once per PR and reason
#   skipped #n (skip list)              once per PR
#   MERGED #n -> sha (through the gate, not by this pass)   a PR the gate merged from another run (mergewait.sh, or the coordinator)
#   EXTERNAL MERGE #n -> sha (not through the gate)         a PR merged by hand, by the web button or `gh pr merge`; the ledger and triggers need to know
#   CLOSED #n (not merged)              a PR closed without merging
#   MAIN RED (...): merging stopped     the base branch is red (a job failed); the sweep exits 5 at once, once per red verdict. Merging stays stopped on
#                                       later passes (nothing is printed again) until the base branch is green: then "MAIN GREEN (...): merging resumed".
#                                       Only the PR that fixes it merges, through ORCH_MAINFIX_OK=1 safemerge.sh <pr> (a deliberate act, never the sweep's).
#   could not list open PRs             gh failed; nothing is swept and no PR is treated as merged
# Exit codes: 0 normal end, 5 the base branch just went red.
# shellcheck source=/dev/null
. "$(dirname "$0")/lib.sh"
interval=${ORCH_SWEEP_INTERVAL:-120}
once=""; minutes=25
case "${1:-}" in --once) once=1;; "") ;; *) minutes=$1;; esac
case "$minutes" in ''|*[!0-9]*) echo "usage: sweep.sh [minutes | --once]" >&2; exit 1;; esac
mkdir -p "$OR_STATE_DIR" || exit 1
open_file="$OR_STATE_DIR/sweep.open"; red_file="$OR_STATE_DIR/sweep.main-red"; skip_file="$OR_STATE_DIR/skip.txt"; merges_log="$OR_STATE_DIR/merges.log"
end=$(( $(date +%s) + minutes*60 ))
seen=$(mktemp); trap 'rm -f "$seen"' EXIT
say() { echo "$(date -u +%H:%MZ) $*"; }
once_per() { grep -qxF -- "$1" "$seen" && return 1; echo "$1" >> "$seen"; return 0; }
while :; do
  # The base branch first: red stops the merging, and says so once.
  or_main_verdict
  stopped=0
  if [ "$OR_MAIN_KIND" = red ]; then
    stopped=1
    if [ "$(cat "$red_file" 2>/dev/null)" != "$OR_MAIN_DETAIL" ]; then
      echo "$OR_MAIN_DETAIL" > "$red_file"
      say "MAIN RED ($OR_BASE: $OR_MAIN_DETAIL): merging stopped"
      exit 5
    fi
  elif [ -f "$red_file" ]; then
    if [ "$OR_MAIN_KIND" = green ]; then rm -f "$red_file"; say "MAIN GREEN ($OR_BASE: $OR_MAIN_DETAIL): merging resumed"; else stopped=1; fi
  fi
  list=$(gh pr list --state open --limit 200 --json number,isDraft --jq '.[]|"\(.number) \(.isDraft)"'); list_rc=$?
  list_ok=1
  if [ "$list_rc" -ne 0 ]; then list_ok=0; once_per "list-failed" && say "could not list open PRs; nothing swept this pass"; fi
  next_open=""
  if [ "$list_ok" = 1 ]; then
    cur=$(printf '%s\n' "$list" | awk 'NF { print $1 }')
    carry=""
    if [ -f "$open_file" ]; then
      while read -r p; do
        [ -n "$p" ] || continue
        printf '%s\n' "$cur" | grep -qx "$p" && continue
        st=$(gh pr view "$p" --json state,mergeCommit --jq '"\(.state) \(.mergeCommit.oid // "")"' 2>/dev/null)
        read -r pr_state pr_sha <<< "$st"
        case "${pr_state:-}" in
          MERGED)
            if grep -qE "^[^ ]+ $p " "$merges_log" 2>/dev/null; then say "MERGED #$p -> ${pr_sha:0:8} (through the gate, not by this pass)"
            else say "EXTERNAL MERGE #$p -> ${pr_sha:0:8} (not through the gate)"; fi;;
          CLOSED) say "CLOSED #$p (not merged)";;
          *) carry="$carry$p"$'\n';;  # not readable, or still open: look again next pass
        esac
      done < "$open_file"
    fi
    next_open=$(printf '%s\n%s' "$cur" "$carry" | awk 'NF && !s[$1]++')
    if [ "$stopped" = 0 ]; then
      skips=$(awk '$1 ~ /^[0-9]+$/ { print $1 }' "$skip_file" 2>/dev/null)
      for pr in $(printf '%s\n' "$list" | awk '$2 == "false" { print $1 }'); do
        if printf '%s\n' "$skips" | grep -qx "$pr"; then once_per "skip:$pr" && say "skipped #$pr (skip list)"; continue; fi
        read -r p n <<< "$(gh pr checks "$pr" --json bucket --jq '"\([.[]|select(.bucket=="pending")]|length) \(length)"' 2>/dev/null)"
        [ "${n:-0}" -ge "$OR_MIN_CHECKS" ] && [ "$p" = 0 ] || continue
        out=$(bash "$OR_SCRIPT_DIR/safemerge.sh" "$pr" 2>&1); rc=$?
        key="$pr:$(echo "$out" | head -1)"
        # What happens is decided by the gate's exit status, never by words in its output (a check's name is free text): 0 merged, 3 stale CI, 5 red.
        if [ "$rc" = 0 ] && echo "$out" | grep -q "^MERGED"; then
          say "$(echo "$out" | head -1)"
          next_open=$(printf '%s\n' "$next_open" | grep -vx "$pr")
        elif [ "$rc" = 3 ]; then
          if gh pr update-branch "$pr" >/dev/null 2>&1; then say "#$pr branch updated (stale CI: it runs again)"
          else once_per "ub:$pr" && say "#$pr needs its lane: the branch update failed (a conflict?)"; fi
        elif once_per "$key"; then say "$(echo "$out" | head -1)"; fi
      done
    fi
    printf '%s\n' "$next_open" > "$open_file"
  fi
  [ -n "$once" ] && break
  [ "$(date +%s)" -lt "$end" ] || break
  sleep "$interval"
  [ "$(date +%s)" -lt "$end" ] || break
done
say "sweep ended"
exit 0
