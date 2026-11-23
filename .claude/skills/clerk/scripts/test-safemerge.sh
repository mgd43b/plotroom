#!/bin/bash
# Offline GitHub-shaped fixtures for the Plotroom bootstrap gate. Run with /bin/bash.
# shellcheck disable=SC2016 # Fixture mutations bind variables in jq.
set -eu
scripts=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin" "$tmp/baseline" "$tmp/fixture"

export FIXTURE="$tmp/fixture"
export HEAD_SHA=0123456789abcdef0123456789abcdef01234567
export OPENING_SHA=123456789abcdef0123456789abcdef012345678
export OTHER_SHA=23456789abcdef0123456789abcdef0123456789
export RUNS_ENDPOINT="repos/fixture/plotroom/actions/workflows/ci.yml/runs?event=pull_request&head_sha=$HEAD_SHA&per_page=100"
export JOBS_ENDPOINT='repos/fixture/plotroom/actions/runs/456/attempts/2/jobs?per_page=100'
export CHECKS_SUFFIX='check-runs?check_name=Kody%20Code%20Review&filter=latest&per_page=100'

cat > "$tmp/bin/gh" <<'SH'
#!/bin/bash
set -eu
case "$*" in
  'pr view 141 --json headRefOid --jq .headRefOid')
    if [ -e "$FIXTURE/body-read" ] && [ -e "$FIXTURE/final-head" ]; then
      cat "$FIXTURE/final-head"
    else
      jq -r .headRefOid "$FIXTURE/pr.json"
    fi ;;
  'pr view 141 --json body --jq .body') touch "$FIXTURE/body-read"; jq -r .body "$FIXTURE/pr.json" ;;
  'pr checks 141 --json name,bucket --jq '*) jq -r "$7" "$FIXTURE/aggregate.json" ;;
  'run list --branch main --workflow ci.yml --status completed --limit 30 --json databaseId,conclusion,headSha --jq '*|\
  'run list --branch main --workflow CI --status completed --limit 30 --json databaseId,conclusion,headSha --jq '*)
    jq -r "${@: -1}" "$FIXTURE/main.json" ;;
  "api $RUNS_ENDPOINT")
    if [ -e "$FIXTURE/jobs-read" ] && [ -e "$FIXTURE/later-runs.json" ]; then
      cat "$FIXTURE/later-runs.json"
    else
      cat "$FIXTURE/runs.json"
    fi ;;
  "api $JOBS_ENDPOINT") touch "$FIXTURE/jobs-read"; cat "$FIXTURE/jobs.json" ;;
  "api repos/fixture/plotroom/commits/$HEAD_SHA/$CHECKS_SUFFIX") cat "$FIXTURE/current.json" ;;
  "api repos/fixture/plotroom/commits/$OPENING_SHA/$CHECKS_SUFFIX") cat "$FIXTURE/opening.json" ;;
  'api graphql -f query='*)
    if [ "$#" = 10 ] && [ "$5 $6 $7 $8 $9 ${10}" = '-f owner=fixture -f repo=plotroom -F pr=141' ] &&
       [[ "$4" == *'number headRefOid reviewThreads(first: 100) { totalCount pageInfo { hasNextPage } nodes { isResolved comments(first: 1) { totalCount } } }'* ]]; then
      cat "$FIXTURE/threads.json"
    elif [ "$#" = 12 ] && [ "$5 $6 $7 $8 $9 ${11} ${12}" = '-f owner=fixture -f repo=plotroom -f -F pr=141' ] &&
         [[ "$4" == *'pullRequest(number: $pr) { number createdAt }'* ]] &&
         [[ "$4" == *'oid associatedPullRequests(first: 2) { totalCount pageInfo { hasNextPage } nodes { number } }'* ]]; then
      case "${10}" in
        "sha=$HEAD_SHA") cat "$FIXTURE/current-association.json" ;;
        "sha=$OPENING_SHA") cat "$FIXTURE/opening-association.json" ;;
        *) printf '%s\n' "unexpected association arguments: $*" >> "$FIXTURE/unexpected"; exit 99 ;;
      esac
    else
      printf '%s\n' "unexpected gh arguments: $*" >> "$FIXTURE/unexpected"
      exit 99
    fi ;;
  *) printf '%s\n' "unexpected gh arguments: $*" >> "$FIXTURE/unexpected"; exit 99 ;;
esac
SH

cat > "$tmp/bin/git" <<'SH'
#!/bin/bash
set -eu
case "$*" in
  'remote get-url origin') echo 'https://github.com/fixture/plotroom.git' ;;
  'fetch -q origin'|'fetch -q origin pull/141/head') ;;
  "merge-base $HEAD_SHA origin/main") echo "$OPENING_SHA" ;;
  "diff --name-only $OPENING_SHA $HEAD_SHA") echo 'change.sh' ;;
  "diff --name-only $OPENING_SHA origin/main") cat "$FIXTURE/main-files" ;;
  "log --format=%B $OPENING_SHA..$HEAD_SHA") echo 'Refs #141' ;;
  *) printf '%s\n' "unexpected git arguments: $*" >> "$FIXTURE/unexpected"; exit 99 ;;
esac
SH
chmod +x "$tmp/bin/gh" "$tmp/bin/git"

export PATH="$tmp/bin:$PATH"
export ORCH_ROOT="$tmp" ORCH_REPO=fixture/plotroom ORCH_BASE_BRANCH=main ORCH_CI_WORKFLOW=ci.yml
export ORCH_STATE_DIR="$FIXTURE" ORCH_MIN_CHECKS=2 ORCH_DRY=1
unset ORCH_EXPECT_HEAD ORCH_IGNORE_CHECKS ORCH_KEYWORDS_OK ORCH_MAINFIX_OK ORCH_MERGE_METHOD

jq -n --arg sha "$HEAD_SHA" '{headRefOid:$sha, body:"Refs #141"}' > "$tmp/baseline/pr.json"
cat > "$tmp/baseline/aggregate.json" <<'JSON'
[{"name":"check (python 3.14)","bucket":"pass"},{"name":"check (python 3.15)","bucket":"pass"}]
JSON
jq -n --arg sha "$HEAD_SHA" '{total_count:1,workflow_runs:[{id:456,run_attempt:2,created_at:"2026-11-23T12:00:00Z",event:"pull_request",head_sha:$sha,status:"completed",conclusion:"success",pull_requests:[{number:141}]}]}' > "$tmp/baseline/runs.json"
jq -n --arg sha "$HEAD_SHA" '{total_count:2,jobs:["3.14","3.15"] | map({run_id:456,run_attempt:2,head_sha:$sha,name:"check (python \(.))",status:"completed",conclusion:"success"})}' > "$tmp/baseline/jobs.json"
jq -n --arg sha "$HEAD_SHA" '{total_count:1,check_runs:[{name:"Kody Code Review",head_sha:$sha,app:{id:5822892,slug:"kodus-mgd43b"},status:"completed",conclusion:"success",started_at:"2026-11-23T12:00:00Z",output:{summary:"Review finished successfully. Suggestions (if any) were posted as PR/file comments."},pull_requests:[]}]}' > "$tmp/baseline/current.json"
jq -n --arg sha "$HEAD_SHA" '{data:{repository:{pullRequest:{number:141,createdAt:"2026-11-23T11:00:00Z"},object:{oid:$sha,associatedPullRequests:{totalCount:1,nodes:[{number:141}],pageInfo:{hasNextPage:false}}}}}}' > "$tmp/baseline/current-association.json"
jq --arg sha "$OPENING_SHA" '.data.repository.object.oid = $sha' "$tmp/baseline/current-association.json" > "$tmp/baseline/opening-association.json"
jq -n --arg sha "$HEAD_SHA" '{data:{repository:{pullRequest:{number:141,headRefOid:$sha,reviewThreads:{totalCount:0,nodes:[],pageInfo:{hasNextPage:false}}}}}}' > "$tmp/baseline/threads.json"
jq -n --arg sha "$OTHER_SHA" '[{databaseId:876,headSha:$sha,conclusion:"success"}]' > "$tmp/baseline/main.json"
touch "$tmp/baseline/main-files"

reset_fixture() {
  rm -f "$FIXTURE/"*
  cp "$tmp/baseline/"* "$FIXTURE/"
}

mutate() {
  jq --arg head "$HEAD_SHA" --arg opening "$OPENING_SHA" --arg other "$OTHER_SHA" "$2" "$FIXTURE/$1.json" > "$tmp/modified"
  mv "$tmp/modified" "$FIXTURE/$1.json"
}

skip_review() {
  cp "$FIXTURE/current.json" "$FIXTURE/opening.json"
  mutate opening '.check_runs[0] |= (.head_sha = $opening | .started_at = "2026-11-23T11:00:00Z")'
  mutate current '.check_runs[0] |= (.conclusion = "skipped" | .output.summary = "Manual mode requires @kody start-review.")'
  printf '141 %s\n' "$OPENING_SHA" > "$FIXTURE/kody-opening-heads"
}

passed=0
expect() {
  local label=$1 expected_status=$2 expected_message=$3 status=0 output
  shift 3
  output=$("$@" /bin/bash "$scripts/safemerge.sh" 141 2>&1) || status=$?
  if [ -e "$FIXTURE/unexpected" ]; then cat "$FIXTURE/unexpected" >&2; exit 1; fi
  if [ "$status" != "$expected_status" ] || [[ "$output" != *"$expected_message"* ]]; then
    printf 'FAIL %s (expected %s and %s, got %s)\n%s\n' "$label" "$expected_status" "$expected_message" "$status" "$output" >&2
    exit 1
  fi
  printf 'PASS %s\n' "$label"
  passed=$((passed + 1))
}

ci_refused() { expect "$1" 2 'latest PR CI attempt'; }
kody_refused() { expect "$1" 2 'no verified completed Kody review'; }
threads_refused() { expect "$1" 2 'could not read the review threads'; }

reset_fixture
expect 'current-head success with no findings or PR associations' 0 'DRY #141: would merge 01234567'
reset_fixture
expect 'main workflow display name does not change the PR workflow file' 0 'DRY #141: would merge 01234567' env ORCH_CI_WORKFLOW=CI
reset_fixture
mutate current '.check_runs[0] |= (.pull_requests = [{number:141}] | del(.started_at))'
rm "$FIXTURE/current-association.json"
expect 'direct Kody association proves the current PR' 0 'DRY #141: would merge 01234567'
reset_fixture
skip_review
mutate threads '.data.repository.pullRequest.reviewThreads |= (.totalCount = 1 | .nodes = [{isResolved:true,comments:{totalCount:2}}])'
expect 'genuine later skip plus trusted opening success and resolved threads' 0 'DRY #141: would merge 01234567'
reset_fixture
skip_review
mutate opening '.check_runs[0] |= (.pull_requests = [{number:141}] | del(.started_at))'
rm "$FIXTURE/opening-association.json"
expect 'direct Kody association proves the opening PR' 0 'DRY #141: would merge 01234567'

reset_fixture
rm "$FIXTURE/current.json"
kody_refused 'unreadable current evidence'
reset_fixture
mutate current '.check_runs = [] | .total_count = 0'
kody_refused 'missing current evidence'
reset_fixture
mutate current '.check_runs[0] |= (.status = "in_progress" | .conclusion = null)'
kody_refused 'unfinished current check'
reset_fixture
mutate current '.check_runs[0].output.summary = "Review started."'
kody_refused 'progress-only summary even with a successful conclusion'
reset_fixture
mutate current '.check_runs[0].app.id = 1'
kody_refused 'spoofed App slug and check name'
reset_fixture
mutate current '.check_runs[0].app.slug = "different-app"'
kody_refused 'unexpected App slug'
reset_fixture
mutate current '.check_runs[0].head_sha = $opening'
kody_refused 'successful check on a stale head'
reset_fixture
mutate current '.check_runs[0].pull_requests = [{number:141},{number:142}]'
kody_refused 'contradictory PR association'
reset_fixture
rm "$FIXTURE/current-association.json"
kody_refused 'empty current check associations need readable commit evidence'
reset_fixture
mutate current-association '.data.repository.object.associatedPullRequests |= (.totalCount = 2 | .nodes += [{number:142}])'
kody_refused 'a shared current commit cannot establish PR ownership'
reset_fixture
mutate current-association '.data.repository.object.associatedPullRequests.nodes = [{number:142}]'
kody_refused 'current commit evidence names a different PR'
reset_fixture
mutate current-association '.data.repository.object.associatedPullRequests.pageInfo.hasNextPage = true'
kody_refused 'truncated current commit associations'
reset_fixture
mutate current-association '.data.repository.object.oid = $other'
kody_refused 'commit association response belongs to a different SHA'
reset_fixture
mutate current '.check_runs[0].started_at = "2026-11-23T10:59:59Z"'
kody_refused 'unassociated current check predates the PR'
reset_fixture
mutate current 'del(.check_runs[0].started_at)'
kody_refused 'empty current check association needs a check start time'
reset_fixture
mutate current '.check_runs[0].started_at = "2026-11-31T12:00:00Z"'
kody_refused 'impossible check start date is refused'
reset_fixture
mutate current-association 'del(.data.repository.pullRequest.createdAt)'
kody_refused 'empty current check association needs a PR creation time'
reset_fixture
mutate current-association '.data.repository.pullRequest.createdAt = "2026-11-23T11:00:00-07:00"'
kody_refused 'PR creation time must be in standard GitHub UTC form'
reset_fixture
mutate current-association '.data.repository.pullRequest.number = 142'
kody_refused 'creation time belongs to a different PR'
reset_fixture
mutate current '.total_count = 101'
kody_refused 'truncated checks response'
reset_fixture
mutate current '.check_runs += [null] | .total_count = 2'
kody_refused 'malformed checks response with a successful check'
reset_fixture
printf '{}\n' >> "$FIXTURE/current.json"
kody_refused 'multiple checks response envelopes'
reset_fixture
mutate current '.check_runs[1] = (.check_runs[0] | .status = "in_progress" | .conclusion = null) | .total_count = 2'
kody_refused 'ambiguous latest App checks'
reset_fixture
skip_review
mutate current '.check_runs[0].output.summary = "Review skipped due to configuration."'
kody_refused 'unrecognized skip reason'
reset_fixture
skip_review
rm "$FIXTURE/kody-opening-heads"
kody_refused 'skip without trusted opening anchor'
reset_fixture
skip_review
printf '142 %s\n' "$OPENING_SHA" > "$FIXTURE/kody-opening-heads"
kody_refused 'opening anchor belongs to another PR'
reset_fixture
skip_review
printf '141 %s\n' "$OPENING_SHA" >> "$FIXTURE/kody-opening-heads"
kody_refused 'duplicate opening anchor'
reset_fixture
skip_review
printf '141 %s\n' "$HEAD_SHA" > "$FIXTURE/kody-opening-heads"
kody_refused 'opening anchor equals skipped head'
reset_fixture
skip_review
printf '141 not-a-sha\n' > "$FIXTURE/kody-opening-heads"
kody_refused 'malformed opening anchor'
reset_fixture
skip_review
rm "$FIXTURE/kody-opening-heads"
mkfifo "$FIXTURE/kody-opening-heads"
kody_refused 'an opening anchor FIFO is refused without reading it'
reset_fixture
skip_review
rm "$FIXTURE/opening.json"
kody_refused 'skip without readable opening check'
reset_fixture
skip_review
mutate opening '.check_runs[0].pull_requests = [{number:142}]'
kody_refused 'opening check belongs to another PR'
reset_fixture
skip_review
rm "$FIXTURE/opening-association.json"
kody_refused 'empty opening check associations need readable commit evidence'
reset_fixture
skip_review
mutate opening-association '.data.repository.object.associatedPullRequests |= (.totalCount = 2 | .nodes += [{number:142}])'
kody_refused 'a shared opening commit cannot establish PR ownership'
reset_fixture
skip_review
mutate opening-association '.data.repository.object.associatedPullRequests.nodes = []'
kody_refused 'partial opening commit association response'
reset_fixture
skip_review
mutate opening '.check_runs[0].started_at = "2026-11-23T10:59:59Z"'
kody_refused 'unassociated opening check predates the PR despite the anchor'
reset_fixture
skip_review
mutate opening '.check_runs[0].app.id = 1'
kody_refused 'spoofed App on opening head'
reset_fixture
skip_review
mutate opening '.check_runs[0].head_sha = $other'
kody_refused 'opening check is tied to an unrelated head'
reset_fixture
skip_review
mutate opening '.check_runs[0] = .check_runs[0] + {conclusion:"skipped",output:{summary:"Manual mode requires @kody start-review."}}'
kody_refused 'opening skip cannot authorize a later skip'

reset_fixture
mutate threads '.data.repository.pullRequest.reviewThreads |= (.totalCount = 1 | .nodes = [{isResolved:false}])'
expect 'unresolved thread blocks current review' 2 '1 unresolved threads'
skip_review
mutate threads '.data.repository.pullRequest.reviewThreads.nodes[0].comments.totalCount = 2'
expect 'unresolved thread blocks cadence skip' 2 '1 unresolved threads'
reset_fixture
mutate threads '.data.repository.pullRequest.reviewThreads |= (.totalCount = 1 | .nodes = [{isResolved:true,comments:{totalCount:1}}])'
expect 'current-head proof requires resolution alone' 0 'DRY #141: would merge 01234567'
skip_review
expect 'a resolved thread without a reply blocks cadence skip' 2 'review threads without a reply'
mutate threads '.data.repository.pullRequest.reviewThreads.nodes[0].comments.totalCount = "2"'
threads_refused 'cadence skip needs a valid thread comment count'
reset_fixture
mutate threads '.data.repository.pullRequest.reviewThreads.totalCount = 101'
expect 'thread limit is enforced' 2 'more than 100 review threads'
reset_fixture
mutate threads '.data.repository.pullRequest.reviewThreads.totalCount = 1'
threads_refused 'truncated thread response'
reset_fixture
mutate threads '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage = true'
threads_refused 'thread response reports more pages'
reset_fixture
mutate threads '.data.repository.pullRequest.reviewThreads |= (.totalCount = 1 | .nodes = [{}])'
threads_refused 'thread resolution state is missing'
reset_fixture
mutate threads '.errors = [{message:"unexpected GraphQL error"}]'
threads_refused 'GraphQL error with partial data'
reset_fixture
mutate threads '.data.repository.pullRequest.number = 142'
threads_refused 'thread response belongs to another PR'
reset_fixture
mutate threads '.data.repository.pullRequest.headRefOid = $other'
expect 'head moved during gate evaluation' 2 'head moved during the merge check'
reset_fixture
printf '%s\n' "$OTHER_SHA" > "$FIXTURE/final-head"
expect 'head moved during later gate reads' 2 'could not confirm that the head is still 01234567'
reset_fixture
expect 'existing expected-head authorization still applies' 2 'head moved to 01234567' env ORCH_EXPECT_HEAD="$OTHER_SHA"

reset_fixture
mutate runs '.workflow_runs[0].head_sha = $opening'
ci_refused 'prior-head CI workflow'
reset_fixture
mutate runs '.workflow_runs[0].pull_requests = [{number:142}]'
ci_refused 'successful CI for a different PR on the same SHA'
reset_fixture
mutate runs '.workflow_runs[1] = (.workflow_runs[0] | .id += 1 | .created_at = "2026-11-23T12:01:00Z" | .status = "in_progress" | .conclusion = null) | .total_count = 2'
ci_refused 'newest current-head CI run is pending after a success'
mutate runs '.workflow_runs[1].pull_requests = []'
ci_refused 'newer run with ambiguous PR association blocks an older success'
mutate runs '.workflow_runs[1].pull_requests = [{number:142}]'
expect 'newer run for a different PR does not shadow this PR' 0 'DRY #141: would merge 01234567'
reset_fixture
jq '.workflow_runs[0].run_attempt += 1' "$FIXTURE/runs.json" > "$FIXTURE/later-runs.json"
ci_refused 'CI attempt changed while its prior jobs were read'
reset_fixture
jq '.workflow_runs[1] = (.workflow_runs[0] | .id += 1 | .created_at = "2026-11-23T12:01:00Z") | .total_count = 2' "$FIXTURE/runs.json" > "$FIXTURE/later-runs.json"
ci_refused 'a newer successful run appeared while jobs were read'
reset_fixture
mutate runs '.workflow_runs[0].conclusion = "failure"'
ci_refused 'current-head CI run failed'
reset_fixture
mutate runs '.total_count = 101'
ci_refused 'truncated workflow response'
reset_fixture
mutate jobs '.jobs[0].run_attempt = 1'
ci_refused 'Python result from a previous CI attempt'
reset_fixture
mutate jobs '.jobs[0].run_id = 999'
ci_refused 'Python result from a different CI run'
reset_fixture
mutate jobs '.jobs[0].head_sha = $opening'
ci_refused 'Python result from a different head'
reset_fixture
mutate jobs '.jobs[0].conclusion = "skipped"'
ci_refused 'one required Python job was skipped'
reset_fixture
mutate jobs '.jobs = [.jobs[0]] | .total_count = 1'
ci_refused 'one required Python job is missing'
reset_fixture
mutate jobs '.jobs[1].name = .jobs[0].name'
ci_refused 'duplicate Python results cannot replace the other job'
reset_fixture
mutate jobs '.total_count = 101'
ci_refused 'truncated jobs response'

reset_fixture
mutate aggregate '. += [{name:"Other check",bucket:"pending"}]'
expect 'existing aggregate pending check still blocks' 2 '1 checks pending'
reset_fixture
mutate aggregate '. += [{name:"Other check",bucket:"fail"}]'
expect 'existing aggregate failure still blocks' 2 'failing: Other check'
expect 'existing aggregate ignore still works' 0 'DRY #141: would merge 01234567' env ORCH_IGNORE_CHECKS='Other check'
reset_fixture
mutate current '.check_runs[0].conclusion = "failure"'
expect 'aggregate ignore cannot waive the required Kody check' 2 'no verified completed Kody review' env ORCH_IGNORE_CHECKS='Kody Code Review'
reset_fixture
mutate main '.[0].conclusion = "startup_failure"'
expect 'red main still blocks' 5 'latest CI verdict is red'
expect 'authorized main repair still passes' 0 'DRY #141: would merge 01234567' env ORCH_MAINFIX_OK=1
reset_fixture
mutate main '[]'
expect 'main without a verdict still blocks' 2 'main has no usable CI verdict'
reset_fixture
printf 'change.sh\n' > "$FIXTURE/main-files"
expect 'overlap still blocks' 3 'main changed these PR files'
reset_fixture
mutate pr '.body = "Closes #141"'
expect 'closing keywords still block' 4 'closing keywords would close issues'
expect 'authorized closing keywords still pass' 0 'DRY #141: would merge 01234567' env ORCH_KEYWORDS_OK=1
printf '%s fixture cases passed.\n' "$passed"
