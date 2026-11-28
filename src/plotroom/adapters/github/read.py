"""Bounded, repository-scoped GitHub reads shared by the detail fetchers."""

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import httpx

from plotroom.adapters.github import GitHubRateLimited, GitHubUnavailable, is_rate_limited
from plotroom.core.github import PullRequest, valid_git_sha

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


def _merged_at(value: Any) -> bool:
    return datetime.fromisoformat(string(value)).utcoffset() is not None


def _merge_oid(value: Any) -> str | None:
    if value is None:
        return None
    oid = string(value)
    if not valid_git_sha(oid):
        raise ValueError("invalid merge OID")
    return oid


class GitHubNotFound(GitHubUnavailable):
    """A configured GitHub read endpoint did not return its resource."""


@dataclass(frozen=True)
class ReviewThreadPull:
    node_id: str
    author_login: str


@dataclass(frozen=True)
class _ObservedPage:
    records: list[Any]
    more: bool
    etag: str | None
    last_page: int | None
    has_next: bool


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
        artifact_redirect: bool = False,
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
            if (response.status_code == 304 and etag is not None) or (
                response.status_code == 302 and artifact_redirect
            ):
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

    async def artifact_location(self, client: httpx.AsyncClient, token: str, artifact: int) -> str:
        """Read a signed artifact location without following it with API credentials."""
        _, response = await self._read(
            client,
            token,
            f"{self.endpoint}/actions/artifacts/{number(artifact)}/zip",
            artifact_redirect=True,
        )
        locations = response.headers.get_list("location")
        if response.status_code != 302 or len(locations) != 1:
            raise GitHubUnavailable("GitHub did not return one artifact download location")
        return locations[0]

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
        return result.records, result.more

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
    ) -> _ObservedPage | None:
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
        last = response.links.get("last") if verify_terminal else None
        last_page = None
        if last is not None:
            try:
                last_page = int(httpx.URL(last["url"]).params["page"])
            except KeyError, ValueError, httpx.InvalidURL:
                raise GitHubUnavailable("GitHub returned invalid pagination") from None
            valid_last = last_page == page if link is None else last_page > page
            if not valid_last:
                raise GitHubUnavailable("GitHub returned incomplete pagination")
        if link is None:
            # Array endpoints cannot give a total. A full page might be the
            # last one, so probe by number instead of trusting an absent Link.
            return _ObservedPage(
                records, verify_terminal and len(records) == 100, current_etag, last_page, False
            )
        try:
            next_page = int(httpx.URL(link["url"]).params["page"])
        except KeyError, ValueError, httpx.InvalidURL:
            raise GitHubUnavailable("GitHub returned invalid pagination") from None
        if next_page != page + 1:
            raise GitHubUnavailable("GitHub returned invalid pagination")
        return _ObservedPage(records, True, current_etag, last_page, True)

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
        max_pages: int = MAX_PAGES,
        require_full_pages: bool = False,
    ) -> AsyncIterator[Row]:
        async def read(index: int, etag: str | None = None) -> _ObservedPage | None:
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

        prior = None
        advertised_last = None
        expected_nonempty = False
        for page in range(1, max_pages + 1 + verify_terminal):
            observed = await read(page)
            assert observed is not None
            records, more = observed.records, observed.more
            if page > max_pages and (records or more):
                break
            if require_full_pages:
                if expected_nonempty and not records:
                    raise GitHubUnavailable("GitHub returned an empty advertised page")
                expected_nonempty = observed.has_next
                if more and len(records) != 100:
                    raise GitHubUnavailable("GitHub returned a short nonterminal page")
                if observed.last_page is not None:
                    if observed.last_page > max_pages:
                        raise GitHubUnavailable("GitHub read exceeded the page limit")
                    if advertised_last not in (None, observed.last_page):
                        raise GitHubUnavailable("GitHub returned conflicting last pages")
                    advertised_last = observed.last_page
                if advertised_last is not None and (
                    (page < advertised_last and not more)
                    or (
                        page == advertised_last
                        and (observed.has_next or (page > 1 and not records))
                    )
                    or (page > advertised_last and (records or more))
                ):
                    raise GitHubUnavailable("GitHub returned incomplete pagination")
            if verify_boundaries and prior is not None:
                confirmed = await read(page - 1, prior.etag)
                if confirmed is not None and (
                    (confirmed.records, confirmed.more) != (prior.records, prior.more)
                    or (
                        require_full_pages
                        and (confirmed.last_page, confirmed.has_next)
                        != (prior.last_page, prior.has_next)
                    )
                ):
                    raise GitHubUnavailable("GitHub pages shifted during the read")
            for row in rows(records):
                yield row
            if recent or not more:
                return
            prior = observed if verify_boundaries else None
        raise GitHubUnavailable("GitHub read exceeded the page limit")

    async def pull_merge_sha(
        self, client: httpx.AsyncClient, token: str, pull: PullRequest
    ) -> str | None:
        repository = self.repository.lower()
        if pull.repository.lower() != repository or not valid_git_sha(pull.head_sha):
            return None
        owner, name = self.repository.split("/")
        result, response = await self._read(
            client,
            token,
            f"{self.api_url}/graphql",
            query={
                "query": PULL_MERGE_SHA_QUERY,
                "variables": {"owner": owner, "name": name, "number": number(pull.number)},
            },
        )
        document = obj(result)
        if "errors" in document and (errors := rows(document["errors"])):
            if response.headers.get("X-RateLimit-Remaining") == "0" or any(
                error.get("type") == "RATE_LIMITED"
                or (
                    isinstance(message := error.get("message"), str)
                    and "rate limit" in message.lower()
                )
                for error in errors
            ):
                raise GitHubRateLimited(response.headers)
            return None
        base = obj(obj(document["data"])["repository"])
        current = obj(base["pullRequest"])
        if (
            string(base["nameWithOwner"]).lower() != repository
            or string(obj(current["repository"])["nameWithOwner"]).lower() != repository
            or number(current["number"]) != pull.number
            or current["merged"] is not True
            or current["state"] != "MERGED"
            or not _merged_at(current["mergedAt"])
            or string(current["headRefOid"]) != pull.head_sha
        ):
            return None
        commit = current["mergeCommit"]
        if commit is not None:
            return _merge_oid(string(obj(commit)["oid"]))

        detail = obj(await self.get(client, token, f"/pulls/{pull.number}"))
        if (
            number(detail["number"]) != pull.number
            or detail["merged"] is not True
            or detail["state"] != "closed"
            or not _merged_at(detail["merged_at"])
            or string(obj(obj(detail["base"])["repo"])["full_name"]).lower() != repository
            or string(obj(detail["head"])["sha"]) != pull.head_sha
        ):
            return None
        return _merge_oid(detail["merge_commit_sha"])

    async def review_threads(
        self,
        client: httpx.AsyncClient,
        token: str,
        pull: int,
        head_sha: str,
        *,
        cadence: ReviewThreadPull | None = None,
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
                    "query": REVIEW_THREADS_QUERY if cadence is None else CADENCE_THREADS_QUERY,
                    "variables": {"owner": owner, "name": name, "number": pull, "after": cursor},
                },
            )
            document = obj(result)
            if document.get("errors"):
                errors = rows(document["errors"])
                if any(error.get("type") == "RATE_LIMITED" for error in errors):
                    raise GitHubRateLimited(response.headers)
                raise GitHubUnavailable("GitHub review thread query failed")
            repository = obj(obj(document["data"])["repository"])
            current = obj(repository["pullRequest"])
            if cadence is not None and (
                string(repository["nameWithOwner"]).lower() != self.repository.lower()
                or number(current["number"]) != pull
                or current["id"] != cadence.node_id
                or string(obj(current["author"])["login"]).lower() != cadence.author_login
            ):
                raise GitHubUnavailable("GitHub returned threads for a different pull request")
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


PULL_MERGE_SHA_QUERY = """
query PullMergeSha($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    pullRequest(number: $number) {
      repository { nameWithOwner }
      number
      merged
      state
      mergedAt
      headRefOid
      mergeCommit { oid }
    }
  }
}
"""


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


CADENCE_THREADS_QUERY = """
query PullCadenceThreads($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    pullRequest(number: $number) {
      id
      number
      author { login }
      headRefOid
      reviewThreads(first: 100, after: $after) {
        totalCount
        nodes {
          id
          isResolved
          comments(first: 100) {
            totalCount
            nodes { id author { __typename login } authorAssociation replyTo { id } }
            pageInfo { hasNextPage }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""
