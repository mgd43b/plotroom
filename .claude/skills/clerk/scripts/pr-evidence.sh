# shellcheck shell=bash
# Evidence predicates for Plotroom's bootstrap gate, sourced by safemerge.sh.

or_latest_pr_ci_attempt() {
  local pr=$1 sha=$2 runs
  runs=$(gh api "repos/$OR_REPO/actions/workflows/ci.yml/runs?event=pull_request&head_sha=$sha&per_page=100") || return 1
  jq -ser --arg sha "$sha" --argjson pr "$pr" '
    def positive_integer: type == "number" and . > 0 and floor == .;
    select(length == 1) | .[0] | .workflow_runs as $runs
    | select(($runs | type) == "array" and .total_count == ($runs | length))
    | select(($runs | length) > 0 and ($runs | length) <= 100)
    | select(all($runs[];
        (.id | positive_integer) and .head_sha == $sha and .event == "pull_request"
        and (.pull_requests | type == "array" and all(.[]; .number | positive_integer))
        and (.created_at | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))))
    | [$runs[] | select(.pull_requests == [] or any(.pull_requests[]; .number == $pr))]
    | max_by([.created_at, .id])
    | select(.run_attempt | positive_integer)
    | select(.status == "completed" and .conclusion == "success")
    | select(any(.pull_requests[]; .number == $pr))
    | "\(.id)/attempts/\(.run_attempt)"
  ' <<< "$runs"
}

or_pr_ci_green() {
  local pr=$1 sha=$2 run jobs
  run=$(or_latest_pr_ci_attempt "$pr" "$sha") || return 1
  jobs=$(gh api "repos/$OR_REPO/actions/runs/$run/jobs?per_page=100") || return 1
  jq -se --arg sha "$sha" --argjson run_id "${run%%/*}" --argjson attempt "${run##*/}" '
    def successful: length == 1 and all(.[]; .status == "completed" and .conclusion == "success");
    select(length == 1) | .[0] | .jobs as $jobs
    | ($jobs | type) == "array" and .total_count == ($jobs | length) and ($jobs | length) <= 100
      and all($jobs[]; .head_sha == $sha and .run_id == $run_id and .run_attempt == $attempt)
      and ([$jobs[] | select(.name == "check (python 3.14)")] | successful)
      and ([$jobs[] | select(.name == "check (python 3.15)")] | successful)
  ' <<< "$jobs" >/dev/null || return 1
  [ "$(or_latest_pr_ci_attempt "$pr" "$sha")" = "$run" ]
}

or_commit_belongs_to_pr() {
  local pr=$1 sha=$2 started_at=$3 evidence query
  # shellcheck disable=SC2016 # These variables are bound by GraphQL.
  query='query($owner: String!, $repo: String!, $sha: GitObjectID!, $pr: Int!) {
    repository(owner: $owner, name: $repo) {
      pullRequest(number: $pr) { number createdAt }
      object(oid: $sha) { ... on Commit {
        oid associatedPullRequests(first: 2) { totalCount pageInfo { hasNextPage } nodes { number } }
      } }
    }
  }'
  evidence=$(gh api graphql -f query="$query" -f owner="$OR_OWNER" -f repo="$OR_NAME" -f sha="$sha" -F pr="$pr") || return 1
  jq -se --arg sha "$sha" --argjson pr "$pr" --arg started_at "$started_at" '
    def utc_epoch: select(type == "string") | . as $text | fromdateiso8601 | select(todateiso8601 == $text);
    select(length == 1) | .[0] | select(type == "object" and (.errors == null or .errors == []))
    | .data.repository
    | select(.pullRequest.number == $pr and ($started_at | utc_epoch) >= (.pullRequest.createdAt | utc_epoch))
    | .object
    | select(.oid == $sha) | .associatedPullRequests
    | .totalCount == 1 and .pageInfo.hasNextPage == false and .nodes == [{number:$pr}]
  ' <<< "$evidence" >/dev/null
}

or_kody_check() {
  local pr=$1 sha=$2 checks proof mode associations started_at
  checks=$(gh api "repos/$OR_REPO/commits/$sha/check-runs?check_name=Kody%20Code%20Review&filter=latest&per_page=100") || return 1
  proof=$(jq -ser --arg sha "$sha" --argjson pr "$pr" '
    select(length == 1) | .[0] | .check_runs as $checks
    | select(($checks | type) == "array" and .total_count == ($checks | length) and ($checks | length) <= 100)
    | select(all($checks[]; type == "object" and (.name | type) == "string"))
    | [$checks[] | select(.name == "Kody Code Review" and .app.id == 5822892 and .app.slug == "kodus-mgd43b")]
    | select(length == 1) | .[0]
    | select(.head_sha == $sha and .status == "completed")
    | select((.pull_requests | type) == "array" and all(.pull_requests[]; .number == $pr))
    | (if .conclusion == "success" and .output.summary == "Review finished successfully. Suggestions (if any) were posted as PR/file comments."
      then "reviewed"
      elif .conclusion == "skipped" and .output.summary == "Manual mode requires @kody start-review."
      then "cadence-skip"
      else empty end) as $mode
    | [$mode, (.pull_requests | length), (if .pull_requests == [] then .started_at | strings else "" end)] | @tsv
  ' <<< "$checks") || return 1
  IFS=$'\t' read -r mode associations started_at <<< "$proof"
  if [ "$associations" = 0 ]; then
    or_commit_belongs_to_pr "$pr" "$sha" "$started_at" || return 1
  fi
  printf '%s\n' "$mode"
}

or_kody_review_complete() {
  local pr=$1 sha=$2 result opening anchors="$OR_STATE_DIR/kody-opening-heads"
  result=$(or_kody_check "$pr" "$sha") || return 1
  if [ "$result" = reviewed ]; then
    printf '%s\n' "$result"
    return 0
  fi
  [ "$result" = cadence-skip ] && [ -f "$anchors" ] && [ -r "$anchors" ] || return 1

  # Only use the opening head recorded for this PR by the clerk or coordinator.
  opening=$(awk -v pr="$pr" '
    $1 == pr { matches++; if (NF == 2) sha = $2 }
    END { if (matches != 1 || sha == "") exit 1; print sha }
  ' "$anchors") || return 1
  [[ "$opening" =~ ^[0-9a-f]{40}$ && "$opening" != "$sha" ]] || return 1
  [ "$(or_kody_check "$pr" "$opening")" = reviewed ] || return 1
  printf '%s\n' "$result"
}

or_pr_threads() {
  local pr=$1 sha=$2 mode=$3 evidence query
  # shellcheck disable=SC2016 # These variables are bound by GraphQL.
  query='query($owner: String!, $repo: String!, $pr: Int!) {
    repository(owner: $owner, name: $repo) {
      pullRequest(number: $pr) {
        number headRefOid reviewThreads(first: 100) { totalCount pageInfo { hasNextPage } nodes { isResolved comments(first: 1) { totalCount } } }
      }
    }
  }'
  evidence=$(gh api graphql -f query="$query" -f owner="$OR_OWNER" -f repo="$OR_NAME" -F pr="$pr") || return 1
  jq -ser --arg sha "$sha" --argjson pr "$pr" --arg mode "$mode" '
    select(length == 1) | .[0] | select(type == "object" and (.errors == null or .errors == []))
    | .data.repository.pullRequest
    | select(.number == $pr and (.headRefOid | type) == "string")
    | if .headRefOid != $sha then "moved"
      else .reviewThreads
        | select(.totalCount | type == "number" and . >= 0 and floor == .)
        | if .totalCount > 100 then "many"
          else select(.pageInfo.hasNextPage == false and (.nodes | type) == "array" and (.nodes | length) == .totalCount)
            | select(all(.nodes[]; (.isResolved | type) == "boolean"))
            | if $mode == "cadence-skip" then
                select(all(.nodes[]; .comments.totalCount | type == "number" and . >= 1 and floor == .))
              end
            | ([.nodes[] | select(.isResolved == false)] | length) as $unresolved
            | if $unresolved > 0 then $unresolved
              elif $mode == "cadence-skip" and any(.nodes[]; .comments.totalCount < 2) then "unanswered"
              else 0 end
          end
      end
  ' <<< "$evidence"
}
