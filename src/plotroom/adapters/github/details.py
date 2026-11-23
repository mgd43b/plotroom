"""Poll GitHub's read projections on the project's existing GitHub cadence."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import httpx

from plotroom.adapters.github import GitHubRateLimited, GitHubUnavailable, LocalGitHubCredential
from plotroom.adapters.github.ci import fetch_checks, fetch_main
from plotroom.adapters.github.issue_details import fetch_comments, fetch_issue_tree, fetch_issues
from plotroom.adapters.github.pr_details import fetch_files, fetch_labels, fetch_threads
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.core.events import format_time
from plotroom.core.github import DETAILS, PULL_REQUESTS, FetchCursor, FetchRetry, apply_comments
from plotroom.core.github_projection import (
    CHECKS,
    COMMENTS,
    FILES,
    ISSUE_TREE,
    ISSUES,
    LABELS,
    MAIN,
    THREADS,
    GitHubResource,
    apply_resource,
    main_verdict,
)
from plotroom.core.ledger import Ledger
from plotroom.ports.clock import Clock

log = logging.getLogger("plotroom.github")


class GitHubDetailPoller:
    def __init__(
        self,
        ledger: Ledger,
        clock: Clock,
        api: GitHubReadClient,
        credential: LocalGitHubCredential,
        image_jobs: tuple[str, ...] = (),
    ) -> None:
        self.ledger = ledger
        self.clock = clock
        self.api = api
        self.credential = credential
        self.image_jobs = image_jobs

    async def cooling_down(self) -> bool:
        repository = self.api.repository
        retry = await self.ledger.fetch_retry(repository, DETAILS)
        pr_retry = await self.ledger.fetch_retry(repository, PULL_REQUESTS)
        now = self.clock.now()
        return any(row and datetime.fromisoformat(row.until) > now for row in (retry, pr_retry))

    async def record_rate_limit(self, exc: GitHubRateLimited) -> None:
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

    async def _poll_resources(self, client: httpx.AsyncClient, token: str) -> None:
        api = self.api
        main = await self._refresh(
            MAIN,
            "",
            lambda previous: fetch_main(api, client, token, self.image_jobs, previous),
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
        targets: dict[str, list[int]] = {}
        if main is not None:
            targets[main.attributes["head_sha"]] = []
            verdict = main_verdict(main)
            if verdict is not None:
                targets[verdict.fields["head_sha"]] = []
        pulls = await self.ledger.github_pull_targets(api.repository)
        for pull in pulls:
            targets.setdefault(pull.head_sha, []).append(pull.number)
        for sha, numbers in targets.items():
            await self._refresh(
                CHECKS,
                sha,
                lambda _, sha=sha: fetch_checks(api, client, token, sha),
                pulls=tuple(numbers),
            )
        for pull in pulls:
            complete = True
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
                )
                complete = complete and result is not None
            check = await self.ledger.github_resource(api.repository, CHECKS, pull.head_sha)
            complete = complete and check is not None and check.complete
            watermark = f"{pull.updated_at}:{pull.version}" if complete else ""
            await self._record_pull_attempt(pull.number, watermark)

        if issues is not None:
            await self._refresh(
                ISSUE_TREE,
                "",
                lambda previous: fetch_issue_tree(api, client, token, issues, previous),
            )
        else:
            await self._mark_incomplete(ISSUE_TREE, "")

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
        baseline = await self.ledger.fetch_cursor(repository, COMMENTS)
        try:
            comments, watermark = await fetch_comments(
                self.api, client, token, baseline.watermark if baseline else "", numbers
            )
        except GitHubRateLimited:
            raise
        except (
            GitHubUnavailable,
            httpx.RequestError,
            KeyError,
            ValueError,
            TypeError,
            OverflowError,
        ):
            log.warning(
                "GitHub issue comment refresh failed", extra={"project": self.ledger.project_id}
            )
            return
        await self.ledger.run(
            "system",
            lambda uow: apply_comments(
                uow, repository, baseline, comments, watermark, format_time(self.clock.now())
            ),
        )

    async def _mark_incomplete(
        self, resource: str, scope: str, pulls: tuple[int, ...] = ()
    ) -> None:
        await self.ledger.run(
            "system",
            lambda uow: apply_resource(
                uow,
                self.api.repository,
                resource,
                scope,
                GitHubResource((), complete=False),
                format_time(self.clock.now()),
                pulls=pulls,
            ),
        )

    async def _refresh(
        self,
        resource: str,
        scope: str,
        read: Callable[[GitHubResource | None], Awaitable[GitHubResource]],
        *,
        pulls: tuple[int, ...] = (),
    ) -> GitHubResource | None:
        repository = self.api.repository
        previous = await self.ledger.github_resource(repository, resource, scope)
        try:
            result = await read(previous)
        except GitHubRateLimited:
            await self._mark_incomplete(resource, scope, pulls)
            raise
        except (
            GitHubUnavailable,
            httpx.RequestError,
            KeyError,
            ValueError,
            TypeError,
            OverflowError,
        ):
            if previous is None or previous.complete:
                log.warning(
                    "GitHub projection refresh failed",
                    extra={"project": self.ledger.project_id, "resource": resource},
                )
                await self._mark_incomplete(resource, scope, pulls)
            return None
        await self.ledger.run(
            "system",
            lambda uow: apply_resource(
                uow, repository, resource, scope, result, format_time(self.clock.now()), pulls=pulls
            ),
        )
        return result

    async def issue_tree(self) -> tuple[dict[str, Any], ...]:
        snapshot = await self.ledger.github_resource(self.api.repository, ISSUE_TREE)
        if snapshot is None or not snapshot.complete:
            raise ValueError("GitHub issue tree is incomplete")
        return tuple(row.details for row in snapshot.records)
