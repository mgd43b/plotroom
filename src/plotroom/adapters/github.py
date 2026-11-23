"""Read-only GitHub pull request intake."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import httpx

from plotroom.core.events import format_time
from plotroom.core.github import PULL_REQUESTS, ObservedPull, apply_pull_poll
from plotroom.core.ledger import Ledger
from plotroom.ports.clock import Clock

log = logging.getLogger("plotroom.github")


class GitHubUnavailable(Exception):
    """A provider error with a message safe to expose to the project."""


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
                candidate = (
                    await asyncio.to_thread(self._file.read_text, encoding="utf-8")
                ).strip()
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
    return ObservedPull(
        number, branch, sha, "merged" if merged_at is not None else state, normalized, body or ""
    )


class PullRequestFetcher:
    """Fetch one resource. Pagination stays on the configured repository endpoint."""

    def __init__(self, api_url: str, repository: str) -> None:
        self._endpoint = httpx.URL(f"{api_url}/repos/{repository}/pulls")

    async def fetch(self, client: httpx.AsyncClient, token: str) -> tuple[ObservedPull, ...]:
        url = self._endpoint.copy_merge_params(
            {"state": "all", "sort": "updated", "direction": "desc", "per_page": "100"}
        )
        seen_pages: set[httpx.URL] = set()
        pulls: dict[int, ObservedPull] = {}
        while True:
            if url in seen_pages:
                raise GitHubUnavailable("GitHub returned repeated pagination")
            seen_pages.add(url)
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2026-03-10",
                },
            )
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
                for row in cast("list[Any]", rows):
                    observed = _pull(row)
                    previous = pulls.get(observed.number)
                    if previous is None or previous.updated_at < observed.updated_at:
                        pulls[observed.number] = observed
            except ValueError, OverflowError:
                raise GitHubUnavailable(
                    "GitHub returned an invalid pull request response"
                ) from None
            link = response.links.get("next")
            if link is None:
                return tuple(pulls.values())
            url = response.url.join(link["url"])
            if url.copy_with(query=None) != self._endpoint:
                raise GitHubUnavailable("GitHub pagination left the configured repository endpoint")


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

    async def poll(self, client: httpx.AsyncClient) -> bool:
        baseline = await self._ledger.fetch_cursor(self._repo, PULL_REQUESTS)
        try:
            token = await self._credential.token()
            pulls = await self._fetcher.fetch(client, token)
        except (GitHubUnavailable, httpx.RequestError) as exc:
            reason = str(exc) if isinstance(exc, GitHubUnavailable) else "GitHub request failed"
            if self._ledger.github_error != reason:
                log.warning(reason, extra={"project": self._ledger.project_id})
            self._ledger.github_error = reason
            return False
        as_of = format_time(self._clock.now())
        committed = await self._ledger.run(
            "system", lambda uow: apply_pull_poll(uow, self._repo, baseline, pulls, as_of)
        )
        if committed:
            self._ledger.github_error = None
        return committed

    async def run(self) -> None:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            while True:
                await self.poll(client)
                await asyncio.sleep(self._seconds)
