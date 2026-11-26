"""Read-only GitHub pull request intake."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, cast

import httpx

from plotroom._secret_file import read_secret_file
from plotroom.core.events import format_time
from plotroom.core.github import DETAILS, PULL_REQUESTS, FetchRetry, ObservedPull, apply_pull_poll
from plotroom.core.ledger import Ledger
from plotroom.ports.clock import Clock

log = logging.getLogger("plotroom.github")
RECONCILE_EVERY = 60
POLL_OVERLAP = timedelta(minutes=5)


class GitHubUnavailable(Exception):
    """A provider error with a message safe to expose to the project."""


class GitHubRateLimited(GitHubUnavailable):
    def __init__(self, headers: httpx.Headers) -> None:
        super().__init__("GitHub rate limit reached")
        self._retry_after = headers.get("Retry-After")
        self._reset = (
            headers.get("X-RateLimit-Reset")
            if headers.get("X-RateLimit-Remaining") == "0"
            else None
        )

    def retry_at(self, now: datetime, fallback: int) -> datetime:
        if self._retry_after is not None:
            try:
                if self._retry_after.isdecimal():
                    return now + timedelta(seconds=max(1, int(self._retry_after)))
                parsed = parsedate_to_datetime(self._retry_after)
                return max(now + timedelta(seconds=1), parsed.replace(tzinfo=parsed.tzinfo or UTC))
            except ValueError, OverflowError:
                pass
        if self._reset is not None:
            try:
                return max(
                    now + timedelta(seconds=1), datetime.fromtimestamp(int(self._reset), tz=UTC)
                )
            except ValueError, OverflowError, OSError:
                pass
        return now + timedelta(seconds=fallback)


def is_rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    if response.headers.get("X-RateLimit-Remaining") == "0" or "Retry-After" in response.headers:
        return True
    try:
        payload: Any = response.json()
    except ValueError, UnicodeError:
        return False
    message = cast("dict[str, Any]", payload).get("message") if isinstance(payload, dict) else None
    return isinstance(message, str) and "rate limit" in message.lower()


def github_secret_name(project_id: str) -> str:
    return f"PLOTROOM_{project_id.upper().replace('-', '_')}_GITHUB_TOKEN"


class LocalGitHubCredential:
    def __init__(self, name: str, environment: Mapping[str, str], file: Path | None) -> None:
        self._name = name
        self._env = environment
        self._file = file

    async def token(self) -> str:
        candidate = self._env.get(self._name, "").strip()
        if not candidate and self._file is not None:
            try:
                candidate = await read_secret_file(self._file)
            except OSError, UnicodeError:
                raise GitHubUnavailable(
                    f"cannot read the configured GitHub token file ({self._name})"
                ) from None
        if not candidate:
            raise GitHubUnavailable(f"GitHub token missing ({self._name})")
        if (
            not candidate.isascii()
            or not candidate.isprintable()
            or any(c.isspace() for c in candidate)
        ):
            raise GitHubUnavailable(f"GitHub token is invalid ({self._name})")
        return candidate


def _pull(value: Any) -> ObservedPull:
    if not isinstance(value, dict):
        raise ValueError("expected a pull request object")
    row = cast("dict[str, Any]", value)
    number, state, updated_at = row.get("number"), row.get("state"), row.get("updated_at")
    head: Any = row.get("head")
    body = row.get("body")
    if (
        type(number) is not int
        or not 0 < number <= 2**63 - 1
        or state not in ("open", "closed")
        or not isinstance(updated_at, str)
        or "merged_at" not in row
        or not isinstance(head, dict)
        or (body is not None and not isinstance(body, str))
    ):
        raise ValueError("incomplete pull request")
    fields = cast("dict[str, Any]", head)
    branch, sha = fields.get("ref"), fields.get("sha")
    if not isinstance(branch, str) or not branch or not isinstance(sha, str) or not sha:
        raise ValueError("incomplete pull request head")
    merged_at = row["merged_at"]
    if merged_at is not None and (state != "closed" or not isinstance(merged_at, str)):
        raise ValueError("invalid merged pull request")
    normalized = format_time(datetime.fromisoformat(updated_at))
    head_repo: Any = fields.get("repo")
    full_name: Any = (
        cast("dict[str, Any]", head_repo).get("full_name") if isinstance(head_repo, dict) else None
    )
    head_repository = full_name.lower() if isinstance(full_name, str) and full_name else None
    draft = row.get("draft")
    if draft is not None and type(draft) is not bool:
        raise ValueError("invalid pull request draft state")
    merge_sha = row.get("merge_commit_sha")
    if (
        merged_at is not None
        and merge_sha is not None
        and (not isinstance(merge_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", merge_sha))
    ):
        raise ValueError("invalid merge commit SHA")
    return ObservedPull(
        number,
        branch,
        sha,
        head_repository,
        "merged" if merged_at is not None else state,
        normalized,
        body or "",
        draft,
        merge_sha if merged_at is not None else None,
    )


class PullRequestFetcher:
    """Fetch one resource. Pagination stays on the configured repository endpoint."""

    def __init__(self, api_url: str, repository: str) -> None:
        self._endpoint = httpx.URL(f"{api_url}/repos/{repository}/pulls")

    async def fetch(
        self, client: httpx.AsyncClient, token: str, floor: str | None
    ) -> tuple[ObservedPull, ...]:
        query = {"state": "all", "sort": "updated", "direction": "desc", "per_page": "100"}
        page = 1
        pulls: dict[int, ObservedPull] = {}
        while True:
            response = await client.get(
                self._endpoint,
                params={**query, "page": str(page)},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2026-03-10",
                },
            )
            if is_rate_limited(response):
                raise GitHubRateLimited(response.headers)
            if response.status_code in (401, 403, 404):
                raise GitHubUnavailable("GitHub denied access to the configured repository")
            if response.status_code != 200:
                raise GitHubUnavailable(
                    f"GitHub pull request poll returned HTTP {response.status_code}"
                )
            try:
                rows: Any = response.json()
                if not isinstance(rows, list):
                    raise ValueError("expected a list of pull requests")
                reached_floor = False
                for row in cast("list[Any]", rows):
                    observed = _pull(row)
                    if floor is not None and observed.updated_at < floor:
                        reached_floor = True
                        continue
                    previous = pulls.get(observed.number)
                    if previous is None or previous.updated_at < observed.updated_at:
                        pulls[observed.number] = observed
            except ValueError, OverflowError:
                raise GitHubUnavailable(
                    "GitHub returned an invalid pull request response"
                ) from None
            link = response.links.get("next")
            if link is None or reached_floor:
                return tuple(pulls.values())
            # GitHub can use its numeric repository route in Link. Only the page number
            # is needed, so every authenticated request stays on the configured endpoint.
            try:
                next_page = int(httpx.URL(link["url"]).params["page"])
            except KeyError, ValueError, httpx.InvalidURL:
                raise GitHubUnavailable("GitHub returned invalid pull request pagination") from None
            if next_page != page + 1:
                raise GitHubUnavailable("GitHub returned invalid pull request pagination")
            page = next_page


class GitHubPoller:
    def __init__(
        self,
        ledger: Ledger,
        clock: Clock,
        repo: str,
        credential: LocalGitHubCredential,
        fetcher: PullRequestFetcher,
        seconds: int,
    ) -> None:
        self._ledger = ledger
        self._clock = clock
        self._repo = repo
        self._credential = credential
        self._fetcher = fetcher
        self._seconds = seconds
        self._retry_at: datetime | None = None

    async def poll(self, client: httpx.AsyncClient) -> bool:
        retry = await self._ledger.fetch_retry(self._repo, PULL_REQUESTS)
        detail_retry = await self._ledger.fetch_retry(self._repo, DETAILS)
        self._retry_at = max(
            (datetime.fromisoformat(row.until) for row in (retry, detail_retry) if row),
            default=None,
        )
        if self._retry_at is not None and self._clock.now() < self._retry_at:
            await self._ledger.set_github_error("GitHub rate limit reached")
            return False
        baseline = await self._ledger.fetch_cursor(self._repo, PULL_REQUESTS)
        # Inclusive overlap tolerates equal timestamps and rows moving between pages.
        # A periodic full reconciliation also refreshes old records.
        floor = None
        if baseline and baseline.watermark and baseline.generation % RECONCILE_EVERY:
            floor = format_time(datetime.fromisoformat(baseline.watermark) - POLL_OVERLAP)
        try:
            token = await self._credential.token()
            pulls = await self._fetcher.fetch(client, token, floor)
        except (GitHubUnavailable, httpx.RequestError) as exc:
            if isinstance(exc, GitHubRateLimited):
                backoff = retry.next_backoff if retry is not None else 60
                self._retry_at = exc.retry_at(self._clock.now(), backoff)
                deadline = FetchRetry(format_time(self._retry_at), min(backoff * 2, 900))
                await self._ledger.run(
                    "system", lambda uow: uow.save_fetch_retry(self._repo, PULL_REQUESTS, deadline)
                )
            reason = str(exc) if isinstance(exc, GitHubUnavailable) else "GitHub request failed"
            if self._ledger.github_error != reason:
                log.warning(reason, extra={"project": self._ledger.project_id})
            await self._ledger.set_github_error(reason)
            return False
        as_of = format_time(self._clock.now())
        committed = await self._ledger.run(
            "system", lambda uow: apply_pull_poll(uow, self._repo, baseline, pulls, as_of)
        )
        if committed:
            await self._ledger.set_github_error(None)
            self._retry_at = None
        return committed

    async def run(
        self, after_pull: Callable[[httpx.AsyncClient], Awaitable[None]] | None = None
    ) -> None:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            while True:
                committed = await self.poll(client)
                if committed and after_pull is not None:
                    await after_pull(client)
                if self._retry_at is None:
                    delay = self._seconds
                else:
                    delay = max(self._seconds, (self._retry_at - self._clock.now()).total_seconds())
                await asyncio.sleep(delay)
