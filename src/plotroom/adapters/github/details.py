"""Poll GitHub's read projections on the project's existing GitHub cadence."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Any

import httpx

from plotroom.adapters.github import (
    GitHubCredential,
    GitHubRateLimited,
    GitHubUnavailable,
    repository_client,
)
from plotroom.adapters.github.ci import (
    JUnitBudget,
    fetch_checks,
    fetch_comparison,
    fetch_default_branch_head,
    fetch_main,
    fetch_main_ci,
)
from plotroom.adapters.github.deployments import poll_deployments
from plotroom.adapters.github.issue_details import (
    IssueRelationshipReadError,
    fetch_comment_backfill,
    fetch_comments,
    fetch_issue_comments,
    fetch_issue_tree,
    fetch_issues,
)
from plotroom.adapters.github.pr_details import (
    fetch_files,
    fetch_labels,
    fetch_pull_text,
    fetch_threads,
)
from plotroom.adapters.github.pull_comparison import fetch_pull_comparison
from plotroom.adapters.github.read import GitHubReadClient, obj, string
from plotroom.core.events import format_time
from plotroom.core.github import (
    DETAILS,
    PULL_REQUESTS,
    FetchCursor,
    FetchRetry,
    PullRequest,
    apply_ask_comment_history,
    apply_comments,
    apply_merge_sha_backfill,
)
from plotroom.core.github_projection import (
    ASK_COMMENT_HISTORY,
    CHECKS,
    COMMENT_BACKFILL,
    COMMENTS,
    FILES,
    ISSUE_PAGINATION_VERIFIED,
    ISSUE_RELATION_RETRY,
    ISSUE_TREE,
    ISSUES,
    LABELS,
    MAIN,
    MAIN_CI,
    MAIN_CI_WINDOW,
    PULL_TEXT,
    THREADS,
    GitHubRecord,
    GitHubResource,
    apply_resource,
    ask_history_version,
    main_verdict,
)
from plotroom.core.ledger import Ledger
from plotroom.core.merge_flakes import (
    FLAKE_COMPARISONS,
    MainPair,
    main_pairs,
    main_recovery_check_ids,
    main_runs,
    record_main_flakes,
    record_pull_flakes,
    record_same_head,
)
from plotroom.core.merge_gate import GateSnapshot
from plotroom.core.merge_overlap import PULL_COMPARISON, bind_overlap_labels
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.clock import Clock

log = logging.getLogger("plotroom.github")
MAX_NEW_COMPARISONS = 4
MAX_ASK_HISTORIES = 4
MAX_MERGE_SHA_READS = 2
MERGE_SHA_READ_TIMEOUT = 3
_READ_ERRORS = (
    GitHubUnavailable,
    httpx.RequestError,
    KeyError,
    ValueError,
    TypeError,
    OverflowError,
)


class GitHubDetailPoller:
    def __init__(
        self,
        ledger: Ledger,
        clock: Clock,
        api: GitHubReadClient,
        credential: GitHubCredential,
        image_jobs: tuple[str, ...] = (),
        *,
        include_issue_tree: bool = False,
        ci_workflow: str | None = None,
        on_merge_evidence: Callable[[int], Awaitable[object]] | None = None,
    ) -> None:
        self.ledger = ledger
        self.clock = clock
        self.api = api
        self.credential = credential
        self.image_jobs = image_jobs
        self.include_issue_tree = include_issue_tree
        self.ci_workflow = ci_workflow
        self.on_merge_evidence = on_merge_evidence
        self._comparison_cursor = 0
        self._ask_history_offset = 0
        self._junit_offset = 0

    async def confirm_gate(self, snapshot: GateSnapshot) -> GateSnapshot | None:
        """Read authoritative evidence on demand for a pinned clerk recheck."""
        try:
            async with (
                asyncio.timeout(20),
                repository_client(self.credential) as client,
            ):
                return await self.read_gate_snapshot(client, snapshot)
        except GitHubRateLimited as exc:
            await self.record_rate_limit(exc)
            return None
        except (
            GitHubUnavailable,
            httpx.RequestError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            OverflowError,
        ):
            return None

    async def read_gate_snapshot(
        self, client: httpx.AsyncClient, snapshot: GateSnapshot
    ) -> GateSnapshot | None:
        pull = snapshot.pull
        workflow = self.ci_workflow
        if pull is None or workflow is None or await self.cooling_down():
            return None
        api, token = self.api, await self.credential.token()
        observed_at = format_time(self.clock.now())
        main_ci = await fetch_main_ci(api, client, token, workflow, snapshot.main_ci, None)
        branch = main_ci.attributes["branch"]
        head = main_ci.attributes["head_sha"]
        main = GitHubResource(
            (),
            attributes={"branch": branch, "head_sha": head},
            as_of=observed_at,
        )
        checks = await fetch_checks(
            api, client, token, pull.head_sha, snapshot.checks, include_junit=False
        )
        labels = await fetch_labels(api, client, token, pull)
        threads = await fetch_threads(api, client, token, pull)
        text = await fetch_pull_text(api, client, token, pull)
        comparison = await fetch_pull_comparison(
            api,
            client,
            token,
            pull,
            branch,
            head,
            snapshot.comparison,
        )
        current = obj(await api.get(client, token, f"/pulls/{pull.number}"))
        draft = current["draft"]
        identity = obj(current["head"])
        if (
            type(draft) is not bool
            or current["state"] != "open"
            or identity["sha"] != pull.head_sha
            or identity["ref"] != pull.branch
            or string(obj(identity["repo"])["full_name"]).lower() != pull.head_repository
            or format_time(datetime.fromisoformat(string(current["updated_at"]))) != pull.updated_at
        ):
            return None
        if await fetch_default_branch_head(api, client, token) != (branch, head):
            return None
        return replace(
            snapshot,
            pull=replace(pull, draft=draft, as_of=observed_at),
            pull_cursor=None,
            checks=replace(checks, as_of=observed_at),
            main_ci=replace(main_ci, as_of=observed_at),
            main=main,
            threads=replace(threads, as_of=observed_at),
            labels=replace(bind_overlap_labels(labels, snapshot.labels, pull), as_of=observed_at),
            pull_text=replace(text, as_of=observed_at),
            comparison=replace(comparison, as_of=observed_at),
        )

    async def cooling_down(self) -> bool:
        repository = self.api.repository
        retry = await self.ledger.fetch_retry(repository, DETAILS)
        pr_retry = await self.ledger.fetch_retry(repository, PULL_REQUESTS)
        now = self.clock.now()
        return any(row and datetime.fromisoformat(row.until) > now for row in (retry, pr_retry))

    async def record_rate_limit(self, exc: GitHubRateLimited) -> None:
        await self.ledger.set_github_error("GitHub rate limit reached")
        repository = self.api.repository
        retry = await self.ledger.fetch_retry(repository, DETAILS)
        backoff = retry.next_backoff if retry else 60
        until = format_time(exc.retry_at(self.clock.now(), backoff))
        next_retry = FetchRetry(max(until, retry.until) if retry else until, min(backoff * 2, 900))
        await self.ledger.run(
            "system", lambda uow: uow.save_fetch_retry(repository, DETAILS, next_retry)
        )

    async def poll(self, client: httpx.AsyncClient) -> None:
        repository = self.api.repository
        if await self.cooling_down():
            return
        retry = await self.ledger.fetch_retry(repository, DETAILS)
        try:
            token = await self.credential.token()
            await self._poll_resources(client, token)
            await self._backfill_merge_shas(client, token)
        except GitHubRateLimited as exc:
            await self.record_rate_limit(exc)
        except GitHubUnavailable:
            # The PR poller exposes credential availability in the project snapshot.
            return
        else:
            if retry is not None and await self.ledger.fetch_retry(repository, DETAILS) == retry:
                await self.ledger.run(
                    "system", lambda uow: uow.save_fetch_retry(repository, DETAILS, None)
                )

    async def _backfill_merge_shas(self, client: httpx.AsyncClient, token: str) -> None:
        as_of = format_time(self.clock.now())
        timeout = min(
            MERGE_SHA_READ_TIMEOUT,
            self.ledger.merge_gate_policy.max_age.total_seconds() / (4 * MAX_MERGE_SHA_READS),
        )
        pending = await self.ledger.github_pending_merge_shas(
            self.api.repository, as_of, MAX_MERGE_SHA_READS
        )
        for pull in pending:
            sha = None
            rate_limit = None
            try:
                async with asyncio.timeout(timeout):
                    sha = await self.api.pull_merge_sha(client, token, pull)
            except GitHubRateLimited as exc:
                rate_limit = exc
            except (*_READ_ERRORS, TimeoutError, RecursionError):
                pass  # Optional evidence remains pending, with no provider payload in logs.
            await self.ledger.run(
                "system",
                partial(
                    apply_merge_sha_backfill,
                    target=pull,
                    merge_sha=sha,
                    as_of=as_of,
                ),
            )
            if rate_limit is not None:
                raise rate_limit

    async def _poll_resources(self, client: httpx.AsyncClient, token: str) -> None:
        api = self.api
        main = await self._refresh(
            MAIN,
            "",
            lambda previous: fetch_main(api, client, token, self.image_jobs, previous),
        )
        main_ci = None
        if (workflow := self.ci_workflow) is not None:
            main_ci = await self._refresh(
                MAIN_CI,
                "",
                lambda previous: fetch_main_ci(api, client, token, workflow, previous, main),
            )
        issues = await self._refresh(
            ISSUES, "", lambda previous: fetch_issues(api, client, token, previous)
        )
        if issues is not None:
            numbers = frozenset(
                row.fields["number"]
                for row in issues.records
                if row.kind in ("issue_opened", "issue_closed")
            )
            await self._poll_comments(client, token, numbers)
            await self._poll_ask_histories(client, token, issues)
        targets: dict[str, list[int]] = {}
        history = tuple(
            sorted(
                {
                    run.key: run
                    for run in (*main_runs(main), *main_runs(main_ci, limit=MAIN_CI_WINDOW))
                }.values(),
                key=lambda run: (
                    run.details["created_at"],
                    run.fields["id"],
                    run.fields["attempt"],
                ),
                reverse=True,
            )
        )
        if main is not None:
            targets[main.attributes["head_sha"]] = []
            verdict = main_verdict(main)
            if verdict is not None:
                targets[verdict.fields["head_sha"]] = []
        for run in history:
            targets.setdefault(run.fields["head_sha"], [])
        pulls = await self.ledger.github_pull_targets(api.repository)
        for pull in pulls:
            targets.setdefault(pull.head_sha, []).append(pull.number)
        checks: dict[str, GitHubResource] = {}
        recoveries = main_recovery_check_ids(history)
        junit_budget = JUnitBudget()
        junit_start = self._junit_offset % max(1, len(targets))
        next_junit_start = 0
        for index, (sha, numbers) in enumerate(targets.items()):
            attempts_before = junit_budget.attempts_left
            result = await self._refresh(
                CHECKS,
                sha,
                lambda previous, sha=sha, allow_lookup=index >= junit_start: fetch_checks(
                    api,
                    client,
                    token,
                    sha,
                    previous,
                    recovery_check_ids=recoveries.get(sha, ()),
                    junit_budget=junit_budget,
                    allow_junit_lookup=allow_lookup,
                ),
                pulls=tuple(numbers),
            )
            if result is not None:
                checks[sha] = result
            if junit_budget.attempts_left != attempts_before:
                next_junit_start = index + 1
        # Rotate optional work only. Required observations still run in their usual order.
        self._junit_offset = next_junit_start if junit_budget.exhausted else 0
        for pull in pulls:
            complete = True
            files = None
            for resource, read in (
                (THREADS, fetch_threads),
                (FILES, fetch_files),
                (LABELS, fetch_labels),
            ):
                result = await self._refresh(
                    resource,
                    str(pull.number),
                    lambda _, read=read, pull=pull: read(api, client, token, pull),
                    pulls=(pull.number,),
                    pull=pull,
                )
                complete = complete and result is not None
                if resource == FILES:
                    files = result
            if main is not None and main.complete and self.ci_workflow is not None:
                await self._refresh(
                    PULL_COMPARISON,
                    str(pull.number),
                    lambda previous, pull=pull: fetch_pull_comparison(
                        api,
                        client,
                        token,
                        pull,
                        main.attributes["branch"],
                        main.attributes["head_sha"],
                        previous,
                    ),
                    pull=pull,
                )
            if files is not None and (observed := checks.get(pull.head_sha)) is not None:
                await self.ledger.run(
                    "system",
                    partial(
                        record_pull_flakes,
                        repository=api.repository,
                        pull=pull,
                        checks=observed,
                        files=files,
                    ),
                )
            check = await self.ledger.github_resource(api.repository, CHECKS, pull.head_sha)
            complete = complete and check is not None and check.complete
            watermark = f"{pull.updated_at}:{pull.version}" if complete else ""
            await self._record_pull_attempt(pull.number, watermark)
        await self._poll_main_flakes(client, token, history, checks)
        if self.ci_workflow is not None:
            for pull in pulls:
                await self._refresh(
                    PULL_TEXT,
                    str(pull.number),
                    lambda _, pull=pull: fetch_pull_text(api, client, token, pull),
                    pull=pull,
                )
        if self.on_merge_evidence is not None:
            for pull in pulls:
                await self.on_merge_evidence(pull.number)

        if self.include_issue_tree or await self.ledger.goals_use_blockers():
            if issues is not None:
                await self._refresh(
                    ISSUE_TREE,
                    "",
                    lambda previous: fetch_issue_tree(api, client, token, issues, previous),
                )
            else:
                await self._mark_incomplete(ISSUE_TREE, "")
        await poll_deployments(self.ledger, api, client, token, format_time(self.clock.now()))

    async def _poll_main_flakes(
        self,
        client: httpx.AsyncClient,
        token: str,
        history: tuple[GitHubRecord, ...],
        checks: dict[str, GitHubResource],
    ) -> None:
        api = self.api
        pairs = main_pairs(history, checks)
        distinct = {(pair.base, pair.head): pair for pair in pairs if pair.base != pair.head}
        observations: dict[str, GitHubResource | None] = {}
        pending: list[tuple[str, MainPair]] = []
        for pair in distinct.values():
            key = f"{pair.base}...{pair.head}"
            cached = await self.ledger.github_resource(api.repository, FLAKE_COMPARISONS, key)
            if cached is not None and cached.complete:
                observations[key] = cached
            else:
                pending.append((key, pair))
        if pending:
            offset = self._comparison_cursor % len(pending)
            for key, pair in (pending[offset:] + pending[:offset])[:MAX_NEW_COMPARISONS]:
                observations[key] = await self._refresh(
                    FLAKE_COMPARISONS,
                    key,
                    lambda _, pair=pair: fetch_comparison(api, client, token, pair.base, pair.head),
                )
            self._comparison_cursor = offset + MAX_NEW_COMPARISONS
        for pair in pairs:
            comparison = observations.get(f"{pair.base}...{pair.head}")
            await self.ledger.run(
                "system",
                partial(
                    record_main_flakes, repository=api.repository, pair=pair, comparison=comparison
                ),
            )

    async def _record_pull_attempt(self, number: int, watermark: str) -> None:
        repository = self.api.repository
        await self.ledger.run(
            "system",
            lambda uow: uow.save_fetch_cursor(
                repository,
                f"pull_details/{number}",
                FetchCursor(watermark, format_time(self.clock.now()), 1),
            ),
        )

    async def _poll_comments(
        self, client: httpx.AsyncClient, token: str, numbers: frozenset[int]
    ) -> None:
        repository = self.api.repository
        for resource, fetch in (
            (COMMENTS, fetch_comments),
            (COMMENT_BACKFILL, fetch_comment_backfill),
        ):
            baseline = await self.ledger.fetch_cursor(repository, resource)
            position = baseline.watermark if baseline else ""
            if position == "done":
                continue
            try:
                comments, watermark = await fetch(self.api, client, token, position, numbers)
            except GitHubRateLimited:
                raise
            except _READ_ERRORS:
                log.warning(
                    "GitHub issue comment refresh failed", extra={"project": self.ledger.project_id}
                )
                continue
            await self.ledger.run(
                "system",
                partial(
                    apply_comments,
                    repository=repository,
                    baseline=baseline,
                    comments=comments,
                    watermark=watermark,
                    as_of=format_time(self.clock.now()),
                    cursor_resource=resource,
                ),
            )

    async def _poll_ask_histories(
        self, client: httpx.AsyncClient, token: str, issues: GitHubResource
    ) -> None:
        if not issues.complete:
            return
        repository = self.api.repository
        pending = await self.ledger.pending_ask_issues(repository)
        targets: list[tuple[int, str, int]] = []
        for row in issues.records:
            if row.kind != "issue_closed" or (number := row.fields["number"]) not in pending:
                continue
            coverage = ask_history_version(row)
            if coverage is None:
                continue
            version, count = coverage
            prior = await self.ledger.github_resource(repository, ASK_COMMENT_HISTORY, str(number))
            if prior is None or prior.attributes.get("version") != version:
                targets.append((number, version, count))
        if not targets:
            return
        targets.sort()
        offset = self._ask_history_offset % len(targets)
        self._ask_history_offset = offset + MAX_ASK_HISTORIES
        for number, version, count in (targets[offset:] + targets[:offset])[:MAX_ASK_HISTORIES]:
            try:
                comments = await fetch_issue_comments(
                    self.api, client, token, number, version, count
                )
            except GitHubRateLimited:
                raise
            except _READ_ERRORS:
                log.warning("GitHub ask comment history refresh failed", extra={"issue": number})
                continue
            # Merge the records into the existing comment projection before marking this
            # particular issue version covered. The writer sees the facts and proof together.
            await self.ledger.run(
                "system",
                partial(
                    apply_ask_comment_history,
                    repository=repository,
                    number=number,
                    version=version,
                    comments=comments,
                    as_of=format_time(self.clock.now()),
                ),
            )

    async def _mark_incomplete(
        self,
        resource: str,
        scope: str,
        pulls: tuple[int, ...] = (),
        *,
        retry_issues: tuple[int, ...] = (),
    ) -> None:
        async def write(uow: UnitOfWork) -> None:
            repository = self.api.repository
            attributes: dict[str, Any] = (
                {ISSUE_PAGINATION_VERIFIED: False} if resource == ISSUE_TREE else {}
            )
            if retry_issues:
                previous = await uow.github_resource(repository, resource, scope)
                pending: set[int] = (
                    set(previous.attributes.get(ISSUE_RELATION_RETRY, [])) if previous else set()
                )
                attributes[ISSUE_RELATION_RETRY] = sorted(pending.union(retry_issues))
            await apply_resource(
                uow,
                repository,
                resource,
                scope,
                GitHubResource((), complete=False, attributes=attributes),
                format_time(self.clock.now()),
                pulls=pulls,
            )

        await self.ledger.run("system", write)

    async def _refresh(
        self,
        resource: str,
        scope: str,
        read: Callable[[GitHubResource | None], Awaitable[GitHubResource]],
        *,
        pulls: tuple[int, ...] = (),
        pull: PullRequest | None = None,
    ) -> GitHubResource | None:
        repository = self.api.repository
        previous = await self.ledger.github_resource(repository, resource, scope)
        try:
            result = await read(previous)
        except IssueRelationshipReadError as exc:
            await self._mark_incomplete(resource, scope, pulls, retry_issues=exc.numbers)
            if isinstance(exc.cause, GitHubRateLimited):
                raise exc.cause from exc
            log.warning(
                "GitHub issue relationship refresh failed",
                extra={"project": self.ledger.project_id},
            )
            return None
        except GitHubRateLimited:
            await self._mark_incomplete(resource, scope, pulls)
            raise
        except _READ_ERRORS:
            if previous is None or previous.complete:
                log.warning(
                    "GitHub projection refresh failed",
                    extra={"project": self.ledger.project_id, "resource": resource},
                )
            if resource == ISSUE_TREE or previous is None or previous.complete:
                await self._mark_incomplete(resource, scope, pulls)
            return None

        async def write(uow: UnitOfWork) -> None:
            if (
                resource == ISSUE_TREE
                and await uow.github_resource(repository, resource, scope) != previous
            ):
                return
            if pull is not None:
                current = await uow.pull_request(repository, pull.number)
                if current is None or (pull.head_sha, pull.version, pull.updated_at) != (
                    current.head_sha,
                    current.version,
                    current.updated_at,
                ):
                    return
            observed = result
            if resource == LABELS and pull is not None:
                previous_labels = await uow.github_resource(repository, LABELS, scope)
                observed = bind_overlap_labels(result, previous_labels, pull)
            await apply_resource(
                uow,
                repository,
                resource,
                scope,
                observed,
                format_time(self.clock.now()),
                pulls=pulls,
            )
            if resource == CHECKS:
                await record_same_head(uow, repository, result)

        await self.ledger.run("system", write)
        return result

    async def issue_tree(self) -> tuple[dict[str, Any], ...]:
        snapshot = await self.ledger.github_resource(self.api.repository, ISSUE_TREE)
        if snapshot is None or not snapshot.complete:
            raise ValueError("GitHub issue tree is incomplete")
        return tuple(row.details for row in snapshot.records)
