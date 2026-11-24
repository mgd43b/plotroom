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


def _array(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("expected a list")
    return cast("list[Any]", value)


def rows(value: Any) -> list[Row]:
    return [obj(row) for row in _array(value)]


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
        etag: str | None = None,
    ) -> tuple[Any, httpx.Response]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if etag is not None:
            headers["If-None-Match"] = etag
        async with client.stream(
            "GET" if query is None else "POST",
            url,
            params=params,
            json=query,
            headers=headers,
            follow_redirects=False,
        ) as response:
            if response.status_code == 304 and etag is not None:
                return None, response
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

    async def page(
        self,
        client: httpx.AsyncClient,
        token: str,
        path: str,
        page: int,
        *,
        params: Mapping[str, Any] | None = None,
        key: str | None = None,
        verify_total: bool = False,
        verify_terminal: bool = False,
    ) -> tuple[list[Row], bool]:
        records, more = await self.raw_page(
            client,
            token,
            path,
            page,
            params=params,
            key=key,
            verify_total=verify_total,
            verify_terminal=verify_terminal,
        )
        return rows(records), more

    async def raw_page(
        self,
        client: httpx.AsyncClient,
        token: str,
        path: str,
        page: int,
        *,
        params: Mapping[str, Any] | None = None,
        key: str | None = None,
        verify_total: bool = False,
        verify_terminal: bool = False,
    ) -> tuple[list[Any], bool]:
        """Read a validated page with its members available for individual decoding."""
        result = await self._observe_page(
            client,
            token,
            path,
            page,
            params=params,
            key=key,
            verify_total=verify_total,
            verify_terminal=verify_terminal,
        )
        assert result is not None
        records, more, _ = result
        return records, more

    async def _observe_page(
        self,
        client: httpx.AsyncClient,
        token: str,
        path: str,
        page: int,
        *,
        params: Mapping[str, Any] | None,
        key: str | None,
        verify_total: bool,
        verify_terminal: bool,
        etag: str | None = None,
    ) -> tuple[list[Any], bool, str | None] | None:
        value, response = await self._read(
            client,
            token,
            self.endpoint + path,
            params={**(params or {}), "per_page": 100, "page": page},
            etag=etag,
        )
        if response.status_code == 304:
            return None
        current_etag = response.headers.get("ETag")
        records = _array(obj(value)[key] if key is not None else value)
        if len(records) > 100:
            raise GitHubUnavailable("GitHub returned an oversized page")
        link = response.links.get("next")
        if verify_total:
            total = obj(value).get("total_count")
            covered = (page - 1) * 100 + len(records)
            if type(total) is not int or not (
                total == covered if link is None else len(records) == 100 and total > covered
            ):
                raise GitHubUnavailable("GitHub returned an incomplete result count")
        if link is None:
            # Array endpoints cannot give a total. A full page might be the
            # last one, so probe by number instead of trusting an absent Link.
            last = response.links.get("last") if verify_terminal else None
            if last is not None:
                try:
                    last_page = int(httpx.URL(last["url"]).params["page"])
                except KeyError, ValueError, httpx.InvalidURL:
                    raise GitHubUnavailable("GitHub returned invalid pagination") from None
                if last_page != page:
                    raise GitHubUnavailable("GitHub returned incomplete pagination")
            return records, verify_terminal and len(records) == 100, current_etag
        try:
            next_page = int(httpx.URL(link["url"]).params["page"])
        except KeyError, ValueError, httpx.InvalidURL:
            raise GitHubUnavailable("GitHub returned invalid pagination") from None
        if next_page != page + 1:
            raise GitHubUnavailable("GitHub returned invalid pagination")
        return records, True, current_etag

    async def pages(
        self,
        client: httpx.AsyncClient,
        token: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        key: str | None = None,
        recent: bool = False,
        verify_total: bool = False,
        verify_terminal: bool = False,
        verify_boundaries: bool = False,
    ) -> AsyncIterator[Row]:
        async def read(
            index: int, etag: str | None = None
        ) -> tuple[list[Any], bool, str | None] | None:
            return await self._observe_page(
                client,
                token,
                path,
                index,
                params=params,
                key=key,
                verify_total=verify_total,
                verify_terminal=verify_terminal,
                etag=etag,
            )

        prior: tuple[list[Any], bool, str | None] | None = None
        for page in range(1, MAX_PAGES + 1 + verify_terminal):
            observed = await read(page)
            assert observed is not None
            records, more, _ = observed
            if page > MAX_PAGES and (records or more):
                break
            if verify_boundaries and prior is not None:
                confirmed = await read(page - 1, prior[2])
                if confirmed is not None and confirmed[:2] != prior[:2]:
                    raise GitHubUnavailable("GitHub pages shifted during the read")
            for row in rows(records):
                yield row
            if recent or not more:
                return
            prior = observed if verify_boundaries else None
        raise GitHubUnavailable("GitHub read exceeded the page limit")

    async def review_threads(
        self, client: httpx.AsyncClient, token: str, pull: int, head_sha: str
    ) -> AsyncIterator[Row]:
        owner, name = self.repository.split("/")
        cursor: str | None = None
        cursors: set[str] = set()
        identifiers: set[str] = set()
        expected: int | None = None
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
            current = obj(document["data"]["repository"]["pullRequest"])
            if string(current["headRefOid"]) != head_sha:
                raise GitHubUnavailable("PR head moved during review thread poll")
            connection = obj(current["reviewThreads"])
            total = connection["totalCount"]
            if type(total) is not int or total < 0 or (expected is not None and total != expected):
                raise GitHubUnavailable("GitHub returned an inconsistent review thread count")
            expected = total
            nodes = rows(connection["nodes"])
            if len(nodes) > 100:
                raise GitHubUnavailable("GitHub returned an oversized review thread page")
            for row in nodes:
                identifier = string(row["id"])
                if not identifier or identifier in identifiers:
                    raise GitHubUnavailable("GitHub returned an invalid review thread ID")
                identifiers.add(identifier)
                yield row
            page = obj(connection["pageInfo"])
            more = page["hasNextPage"]
            if more is False and len(identifiers) == expected:
                return
            if more is not True or not nodes or len(identifiers) >= expected:
                raise GitHubUnavailable("GitHub returned an incomplete review thread list")
            cursor = string(page["endCursor"])
            if not cursor or cursor in cursors:
                raise GitHubUnavailable("GitHub returned invalid review thread pagination")
            cursors.add(cursor)
        raise GitHubUnavailable("GitHub review threads exceeded the page limit")


REVIEW_THREADS_QUERY = """
query PullReviewThreads($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      headRefOid
      reviewThreads(first: 100, after: $after) {
        totalCount
        nodes { id isResolved path line }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""
