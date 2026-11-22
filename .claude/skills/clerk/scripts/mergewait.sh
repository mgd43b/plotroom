#!/bin/bash
# mergewait.sh <pr> <sha8>
# Waits for that head's checks to finish (at least ORCH_MIN_CHECKS, and none pending), then ORCH_MERGEWAIT_SETTLE seconds (default 120) for a late review, then runs
# safemerge.sh pinned to that head, and exits with its status. Gives up if the head moves from <sha8>, before or during the wait (exit 2), or after
# ORCH_MERGEWAIT_POLLS polls (default 120, ORCH_MERGEWAIT_POLL seconds apart, default 45; exit 1). Run it from inside the checkout.
# shellcheck source=/dev/null
. "$(dirname "$0")/lib.sh"
pr=$1; sha8=$2
[ -n "$pr" ] && [ -n "$sha8" ] || { echo "usage: mergewait.sh <pr> <sha8>" >&2; exit 1; }
i=0
while [ "$i" -lt "${ORCH_MERGEWAIT_POLLS:-120}" ]; do
  i=$((i + 1))
  head=$(gh pr view "$pr" --json headRefOid --jq .headRefOid)
  case "$head" in "$sha8"*) ;; *) echo "#$pr head moved: $head"; exit 2;; esac
  read -r p n <<< "$(gh pr checks "$pr" --json bucket --jq '"\([.[]|select(.bucket=="pending")]|length) \(length)"' 2>/dev/null)"
  if [ "${n:-0}" -ge "$OR_MIN_CHECKS" ] && [ "$p" = 0 ]; then
    sleep "${ORCH_MERGEWAIT_SETTLE:-120}"
    ORCH_EXPECT_HEAD="$sha8" bash "$OR_SCRIPT_DIR/safemerge.sh" "$pr" 2>&1
    exit $?  # the gate's own status: 0 merged; 2, 3, 4 or 5 refused; 1 failed
  fi
  sleep "${ORCH_MERGEWAIT_POLL:-45}"
done
echo "#$pr timed out"; exit 1
