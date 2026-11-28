"""Read-only synthetic provider coverage for legacy Commit Status evidence."""

from collections import Counter
from typing import Any

import httpx
import pytest

from plotroom.adapters.github import GitHubRateLimited, GitHubUnavailable
from plotroom.adapters.github.commit_statuses import MAX_STATUS_PAGES, fetch_commit_statuses
from plotroom.adapters.github.read import GitHubReadClient

API = "https://api.github.com"
REPOSITORY = "fixture/project"
HEAD = "a" * 40
STATUS_URL = f"{API}/repos/Fixture/Project/statuses/{HEAD}"
PATH = f"/repos/{REPOSITORY}/commits/{HEAD}/statuses"


def item(identifier: int) -> dict[str, Any]:
    return {
        "id": identifier,
        "context": "ci/Lint",
        "state": "success",
        "creator": {"id": 8, "login": "fixture"},
        "url": STATUS_URL,
    }


def verify_request(request: httpx.Request) -> int:
    assert request.method == "GET"
    assert request.url.path == PATH
    assert request.url.params["per_page"] == "100"
    return int(request.url.params["page"])


@pytest.mark.parametrize(
    ("entries", "declare_last"),
    [
        (0, False),
        (1, False),
        (100, False),
        (101, False),
        (201, True),
        (MAX_STATUS_PAGES * 100, False),
        (MAX_STATUS_PAGES * 100, True),
    ],
)
async def test_status_lists_are_bounded_and_prove_terminal_page(
    entries: int, declare_last: bool
) -> None:
    visits: Counter[int] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        page = verify_request(request)
        visits[page] += 1
        start = (page - 1) * 100
        # Keep later pages unlinked, including the full last page and its empty probe.
        headers: dict[str, str] = {}
        if page == 1 and declare_last:
            last = (entries + 99) // 100
            headers["link"] = (
                f'<{API}{PATH}?page=2>; rel="next", <{API}{PATH}?page={last}>; rel="last"'
            )
        return httpx.Response(
            200,
            json=[item(entries - index) for index in range(start, min(entries, start + 100))],
            headers=headers,
        )

    api = GitHubReadClient(API, REPOSITORY)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        statuses = await fetch_commit_statuses(api, client, "fixture", HEAD)
    assert statuses.complete
    assert statuses.attributes == {"repository": REPOSITORY, "head_sha": HEAD}
    assert [row.fields["id"] for row in statuses.records] == list(range(entries, 0, -1))
    assert visits == Counter(
        {**dict.fromkeys(range(1, entries // 100 + 1), 2), entries // 100 + 1: 1}
    )


@pytest.mark.parametrize(
    ("override", "exception"),
    [
        ({"id": True}, ValueError),
        ({"creator": None}, ValueError),
        ({"creator": {"id": 0}}, ValueError),
        ({"context": " "}, ValueError),
        ({"state": "skipped"}, ValueError),
        ({"url": STATUS_URL.replace("api.github.com", "lookalike.invalid")}, ValueError),
        ({"url": STATUS_URL.replace("Project", "OtherProject")}, ValueError),
        ({"url": STATUS_URL.replace(HEAD, "b" * 40)}, ValueError),
        ({"url": f"{STATUS_URL}?ref={HEAD}"}, ValueError),
    ],
)
async def test_malformed_or_wrongly_pinned_status_rows_refuse(
    override: dict[str, Any], exception: type[ValueError]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert verify_request(request) == 1
        return httpx.Response(200, json=[item(2), {**item(1), **override}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(exception):
            await fetch_commit_statuses(GitHubReadClient(API, REPOSITORY), client, "fixture", HEAD)


@pytest.mark.parametrize("failure", ["shift", "cap", "duplicate", "short", "contradictory"])
async def test_incomplete_status_sequences_refuse(failure: str) -> None:
    visits: Counter[int] = Counter()
    boundary_etag = '"page-one"'

    def handler(request: httpx.Request) -> httpx.Response:
        page = verify_request(request)
        visits[page] += 1
        start = 1000 - (page - 1) * 100
        records = [item(identifier) for identifier in range(start, start - 100, -1)]
        headers: dict[str, str] = {}
        if page == 1:
            headers["etag"] = boundary_etag
        if failure == "shift" and visits[page] > 1:
            assert request.headers["if-none-match"] == boundary_etag
            records[0] = item(start + 1)
        if failure == "duplicate" and page == 2:
            records = [item(1000)]
        if failure == "short":
            records = records[:1]
            headers["link"] = f'<{API}{PATH}?page=2>; rel="next"'
        if failure == "contradictory":
            headers["link"] = f'<{API}{PATH}?page=2>; rel="next", <{API}{PATH}?page=1>; rel="last"'
        return httpx.Response(200, json=records, headers=headers)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises((GitHubUnavailable, ValueError)):
            await fetch_commit_statuses(GitHubReadClient(API, REPOSITORY), client, "fixture", HEAD)
        assert max(visits) <= MAX_STATUS_PAGES + 1


@pytest.mark.parametrize(
    ("second_count", "conflicting_link", "error"),
    [
        (2, False, "incomplete pagination"),
        (100, True, "conflicting last pages"),
    ],
)
async def test_declared_last_page_cannot_be_lost(
    second_count: int, conflicting_link: bool, error: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = verify_request(request)
        if page == 1:
            return httpx.Response(
                200,
                json=[item(identifier) for identifier in range(301, 201, -1)],
                headers={
                    "link": f'<{API}{PATH}?page=2>; rel="next", <{API}{PATH}?page=3>; rel="last"'
                },
            )
        assert page == 2
        headers: dict[str, str] = {}
        if conflicting_link:
            headers["link"] = f'<{API}{PATH}?page=3>; rel="next", <{API}{PATH}?page=4>; rel="last"'
        return httpx.Response(
            200,
            json=[item(identifier) for identifier in range(201, 201 - second_count, -1)],
            headers=headers,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubUnavailable, match=error):
            await fetch_commit_statuses(GitHubReadClient(API, REPOSITORY), client, "fixture", HEAD)


@pytest.mark.parametrize(
    ("code", "exception"),
    [(403, GitHubUnavailable), (404, GitHubUnavailable), (429, GitHubRateLimited)],
)
async def test_unavailable_provider_status_is_never_an_empty_list(
    code: int, exception: type[GitHubUnavailable]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert verify_request(request) == 1
        return httpx.Response(code)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(exception):
            await fetch_commit_statuses(GitHubReadClient(API, REPOSITORY), client, "fixture", HEAD)
