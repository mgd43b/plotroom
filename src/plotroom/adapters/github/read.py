"""Bounded, repository-scoped GitHub reads shared by the detail fetchers."""

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

import httpx

from plotroom.adapters.github import GitHubRateLimited, GitHubUnavailable, is_rate_limited

type Row = dict[str, Any]
MAX_PAGES = 30
MAX_RESPONSE = 8 * 1024 * 1024


def obj(value: Any) -> Row:
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    return cast("Row", value)


def rows(value: Any) -> list[Row]:
    if not isinstance(value, list):
        raise ValueError("expected a list")
    return [obj(row) for row in cast("list[Any]", value)]


def string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("expected a string")
    return value


def number(value: Any) -> int:
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ValueError("expected a positive integer")
    return value


class GitHubNotFound(GitHubUnavailable):
    """A configured GitHub read endpoint did not return its resource."""


class GitHubReadClient:
    def __init__(self, api_url: str, repository: str) -> None:
        self.api_url = api_url
        self.repository = repository
        self.endpoint = f"{api_url}/repos/{repository}"

    async def _read(
        self,
        client: httpx.AsyncClient,
        token: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        query: Row | None = None,
    ) -> tuple[Any, httpx.Response]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        async with client.stream(
            "GET" if query is None else "POST",
            url,
            params=params,
            json=query,
            headers=headers,
            follow_redirects=False,
        ) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE:
                    raise GitHubUnavailable("GitHub response exceeded the byte limit")
            if response.status_code == 429 or (
                response.status_code == 403
                and (
                    is_rate_limited(
                        httpx.Response(403, headers=response.headers, content=bytes(body))
                    )
                )
            ):
                raise GitHubRateLimited(response.headers)
            if response.status_code == 404:
                raise GitHubNotFound("GitHub resource is unavailable")
            if response.status_code != 200:
                raise GitHubUnavailable(f"GitHub read returned HTTP {response.status_code}")
            try:
                return json.loads(body), response
            except ValueError:
                raise GitHubUnavailable("GitHub returned invalid JSON") from None

    async def get(
        self,
        client: httpx.AsyncClient,
        token: str,
        path: str,
        params: Mapping[str, Any] | None = None,
    ) -> Row | list[Any]:
        value, _ = await self._read(client, token, self.endpoint + path, params=params)
        if isinstance(value, list):
            return cast("list[Any]", value)
        return obj(value)

    async def pages(
        self,
        client: httpx.AsyncClient,
        token: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        key: str | None = None,
        recent: bool = False,
    ) -> AsyncIterator[Row]:
        for page in range(1, MAX_PAGES + 1):
            value, response = await self._read(
                client,
                token,
                self.endpoint + path,
                params={**(params or {}), "per_page": 100, "page": page},
            )
            for row in rows(obj(value)[key] if key is not None else value):
                yield row
            link = response.links.get("next")
            if link is None or recent:
                return
            try:
                next_page = int(httpx.URL(link["url"]).params["page"])
            except KeyError, ValueError, httpx.InvalidURL:
                raise GitHubUnavailable("GitHub returned invalid pagination") from None
            if next_page != page + 1:
                raise GitHubUnavailable("GitHub returned invalid pagination")
        raise GitHubUnavailable("GitHub read exceeded the page limit")

    async def review_threads(
        self, client: httpx.AsyncClient, token: str, pull: int
    ) -> AsyncIterator[Row]:
        owner, name = self.repository.split("/")
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            result, response = await self._read(
                client,
                token,
                f"{self.api_url}/graphql",
                query={
                    "query": REVIEW_THREADS_QUERY,
                    "variables": {"owner": owner, "name": name, "number": pull, "after": cursor},
                },
            )
            document = obj(result)
            if document.get("errors"):
                errors = rows(document["errors"])
                if any(error.get("type") == "RATE_LIMITED" for error in errors):
                    raise GitHubRateLimited(response.headers)
                raise GitHubUnavailable("GitHub review thread query failed")
            connection = obj(document["data"]["repository"]["pullRequest"]["reviewThreads"])
            for row in rows(connection["nodes"]):
                yield row
            page = obj(connection["pageInfo"])
            more = page["hasNextPage"]
            if more is False:
                return
            cursor = string(page["endCursor"])
            if more is not True or not cursor or cursor in seen:
                raise GitHubUnavailable("GitHub returned invalid review thread pagination")
            seen.add(cursor)
        raise GitHubUnavailable("GitHub review threads exceeded the page limit")


REVIEW_THREADS_QUERY = """
query PullReviewThreads($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        nodes { id isResolved path line }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""
