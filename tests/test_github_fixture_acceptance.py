"""Keep the keeper workflow's phase and provider boundaries executable offline."""

import base64
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripts import github_fixture_acceptance as fixture

from plotroom.app.tokens import TokenRegistry
from plotroom.core.events import Event
from plotroom.core.triggers import TRIGGER_FIRED
from test_github import REPO as LOOPBACK_REPO
from test_github import TOKEN as LOOPBACK_TOKEN
from test_github import FixtureGitHub
from test_github import github_fixture as github_fixture

TIP = "a" * 40
PREFIX = f"/repos/{fixture.REPO}"
FAKE_PAT = "github_pat_fixture"


@pytest.fixture(autouse=True)
def isolate_fixture_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "isolated-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))


class FixtureProvider:
    def __init__(self, denied: str = "") -> None:
        self.denied = denied
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == f"Bearer {FAKE_PAT}"
        assert request.url.path.startswith(PREFIX)
        relative = request.url.path.removeprefix(PREFIX)
        self.requests.append((request.method, relative))
        if relative == self.denied and self.denied:
            return httpx.Response(403, json={"message": "resource denied"})
        if request.method != "GET":
            return httpx.Response(201, json={"created": True})
        if not relative:
            return httpx.Response(
                200,
                json={
                    "full_name": fixture.REPO,
                    "default_branch": "main",
                    "private": False,
                    "fork": False,
                },
            )
        if relative.startswith("/contents/"):
            assert request.url.params["ref"] == TIP
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "type": "file",
                    "sha": TIP,
                    "content": base64.b64encode(b"file").decode(),
                },
            )
        if relative.startswith("/commits/"):
            return httpx.Response(403, json={"message": "Checks unavailable"})
        payloads: dict[str, Any] = {
            "/branches/main": {"commit": {"sha": TIP}},
            "/pulls": [],
            "/actions/runs": {"workflow_runs": []},
            "/issues": [],
            "/deployments": [],
        }
        return httpx.Response(200, json=payloads[relative])


def client(mock: FixtureProvider) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(mock), headers={"Authorization": f"Bearer {FAKE_PAT}"}
    )


async def test_preflight_reports_checks_denial_and_refuses_writes() -> None:
    mock = FixtureProvider()
    async with client(mock) as http:
        provider = fixture.Provider(http, acceptance=False)
        assert await fixture.preflight(provider) == (TIP, 0, None)
        provider.preflight_passed = True
        with pytest.raises(fixture.AcceptanceError, match="mutation before preflight"):
            await provider.request("POST", "/git/refs")
    assert {method for method, _ in mock.requests} == {"GET"}
    async with client(mock) as http:
        scoped = fixture.Provider(http, acceptance=True)
        scoped.preflight_passed = True
        with pytest.raises(fixture.AcceptanceError, match="unapproved fixture mutation"):
            await scoped.request("POST", "/deployments")


async def test_core_preflight_denial_prevents_any_lifecycle(tmp_path: Path) -> None:
    mock = FixtureProvider(denied="/pulls")
    async with client(mock) as http:
        provider = fixture.Provider(http, acceptance=True)
        with pytest.raises(fixture.AcceptanceError, match="GET failed with HTTP 403"):
            await fixture.execute(tmp_path, provider, "42")
        assert not provider.preflight_passed
    assert {method for method, _ in mock.requests} == {"GET"}
    assert not (tmp_path / "fixture.toml").exists()


async def test_snapshot_failure_prevents_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def no_live_stdio(*_: object) -> AsyncGenerator[None]:
        yield None

    async def stale(*_: object) -> None:
        raise fixture.AcceptanceError("fresh file-only snapshot missing")

    monkeypatch.setattr(fixture, "stdio", no_live_stdio)
    monkeypatch.setattr(fixture, "snapshot", stale)
    mock = FixtureProvider()
    async with client(mock) as http:
        provider = fixture.Provider(http, acceptance=True)
        with pytest.raises(fixture.AcceptanceError, match="snapshot missing"):
            await fixture.execute(tmp_path, provider, "42")
        assert not provider.preflight_passed
    assert {method for method, _ in mock.requests} == {"GET"}


async def test_acceptance_preflight_orders_snapshot_and_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace: list[str] = []

    @asynccontextmanager
    async def read_stdio(*_: object) -> AsyncGenerator[None]:
        trace.append("stdio")
        yield None

    async def fresh(*_: object) -> dict[str, Any]:
        trace.append("snapshot")
        return {"github": {"available": True, "as_of": "2026-11-26T00:00:00Z"}}

    async def same_refs(*_: object) -> tuple[str, ...]:
        trace.append("mirror")
        return ("1.1",)

    async def deployment(*_: object) -> None:
        trace.append("report")

    async def lifecycle(*_: object) -> None:
        trace.append("lifecycle")

    async def stop(*_: object) -> None:
        trace.append("stop")

    monkeypatch.setattr(fixture, "stdio", read_stdio)

    def daemon_running(_: Path) -> bool:
        return True

    monkeypatch.setattr(fixture, "locked", daemon_running)
    monkeypatch.setattr(fixture, "snapshot", fresh)
    monkeypatch.setattr(fixture, "mirror", same_refs)
    monkeypatch.setattr(fixture, "deployment", deployment)
    monkeypatch.setattr(fixture, "lifecycle", lifecycle)
    monkeypatch.setattr(fixture, "stop_daemon", stop)
    mock = FixtureProvider()
    async with client(mock) as http:
        await fixture.execute(tmp_path, fixture.Provider(http, acceptance=True), "42")
    assert trace == ["stdio", "snapshot", "mirror", "report", "lifecycle", "stop"]


async def test_lifecycle_uses_exact_head_and_a_stopped_cursor_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plotroom.adapters.store_sqlite import SqliteStore
    from plotroom.core.github import FetchCursor

    first, second, merged = "b" * 40, "c" * 40, "d" * 40
    branch = "plotroom-fixture/42"

    class Repository(FixtureProvider):
        def __init__(self) -> None:
            super().__init__()
            self.tip = TIP
            self.state = "open"

        def __call__(self, request: httpx.Request) -> httpx.Response:
            method = request.method
            path = request.url.path.removeprefix(PREFIX)
            assert request.url.path.startswith(PREFIX)
            self.requests.append((method, path))
            if path == "/branches/main":
                return httpx.Response(200, json={"commit": {"sha": self.tip}})
            if path.endswith("/runs"):
                expected = TIP if request.url.params["event"] == "push" else second
                assert request.url.params["head_sha"] == expected
                return httpx.Response(
                    200,
                    json={
                        "workflow_runs": [
                            {
                                "id": 3,
                                "run_attempt": 1,
                                "head_sha": expected,
                                "head_branch": request.url.params["branch"],
                                "event": request.url.params["event"],
                                "head_repository": {"full_name": fixture.REPO},
                                "conclusion": "success",
                                "status": "completed",
                            }
                        ]
                    },
                )
            if path.endswith("/jobs"):
                return httpx.Response(
                    200,
                    json={
                        "jobs": [{"name": name, "conclusion": "success"} for name in fixture.JOBS]
                    },
                )
            if path == "/git/refs":
                assert request.content
                return httpx.Response(201, json={})
            if path.startswith("/contents/"):
                if method == "PUT":
                    head = first if path.endswith("README.md") else second
                    return httpx.Response(200, json={"commit": {"sha": head}})
                data = "[[" + "entry]]\nid = 1\n" if path.endswith(".toml") else "fixture"
                return httpx.Response(
                    200,
                    json={
                        "type": "file",
                        "encoding": "base64",
                        "sha": TIP,
                        "content": base64.b64encode(data.encode()).decode(),
                    },
                )
            if method == "POST" and path == "/pulls":
                return httpx.Response(201, json=self.pull(first))
            if path == "/pulls/9":
                if method == "PATCH":
                    self.state = json.loads(request.content)["state"]
                    return httpx.Response(200, json={})
                return httpx.Response(200, json=self.pull(second))
            assert (method, path) == ("PUT", "/pulls/9/merge")
            assert json.loads(request.content) == {"sha": second, "merge_method": "merge"}
            self.tip = merged
            return httpx.Response(200, json={"merged": True, "sha": merged})

        def pull(self, head: str) -> dict[str, Any]:
            repo = {"full_name": fixture.REPO}
            return {
                "number": 9,
                "state": self.state,
                "mergeable": True,
                "base": {"repo": repo, "ref": "main", "sha": TIP},
                "head": {"repo": repo, "ref": branch, "sha": head},
            }

    seen: list[tuple[str, str, int]] = []
    checkpoint = 1

    @asynccontextmanager
    async def stdio(*_: object) -> AsyncGenerator[None]:
        yield None

    async def observe(
        _store: object, _client: object, number: int, state: str, head: str, count: int
    ) -> None:
        assert number == 9
        seen.append((state, head, count))

    async def stop(_: Path) -> None:
        nonlocal checkpoint
        checkpoint = 2  # The last poll can commit while the process shuts down.

    async def fresh(*_: object) -> None:
        nonlocal checkpoint
        checkpoint = 3

    async def cursor(*_: object) -> FetchCursor:
        return FetchCursor("2026-11-26T00:00:00Z", "2026-11-26T00:00:00Z", checkpoint)

    async def trigger(_client: object, _number: int, label: str) -> str:
        return label

    async def mirror(
        _provider: object, _store: object, _root: Path, revision: str
    ) -> tuple[str, ...]:
        assert revision == merged
        return ("1.1", "1.2")

    async def events(*_: object) -> None:
        assert checkpoint == 3

    for name, replacement in (
        ("stdio", stdio),
        ("observe_pull", observe),
        ("stop_daemon", stop),
        ("snapshot", fresh),
        ("trigger", trigger),
        ("mirror", mirror),
        ("verify_events", events),
    ):
        monkeypatch.setattr(fixture, name, replacement)
    store = SqliteStore(fixture.PROJECT, tmp_path)
    monkeypatch.setattr(store, "fetch_cursor", cursor)

    async def committed(*_: object) -> list[Event]:
        return [
            Event(
                1,
                "fixture-id",
                "core",
                TRIGGER_FIRED,
                "Fixture already merged",
                None,
                {},
                "",
                "2026-11-26T00:00:00Z",
            )
        ]

    monkeypatch.setattr(store, "events_after", committed)
    mock = Repository()
    try:
        async with client(mock) as http:
            await fixture.lifecycle(
                fixture.Provider(http, acceptance=True),
                store,
                tmp_path / "fixture.toml",
                "bearer-id",
                tmp_path,
                TIP,
                0,
                "42",
            )
    finally:
        await store.close()
    assert seen == [
        ("open", first, 1),
        ("open", second, 1),
        ("closed", second, 0),
        ("open", second, 1),
        ("merged", second, 0),
    ]
    assert [path for method, path in mock.requests if method != "GET"] == [
        "/git/refs",
        "/contents/README.md",
        "/pulls",
        f"/contents/{fixture.STORY}",
        "/pulls/9",
        "/pulls/9",
        "/pulls/9/merge",
    ]


async def test_keeper_stdio_opens_the_daemon_with_the_private_token_file(
    tmp_path: Path, github_fixture: FixtureGitHub
) -> None:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    config = fixture.make_config(directory)
    config.write_text(
        config.read_text().replace(
            f'repository = "{fixture.REPO}"',
            f'repository = "{LOOPBACK_REPO}"\napi_url = "{github_fixture.url}"',
        ),
        encoding="utf-8",
    )
    token = directory / "github-token"
    token.write_text(LOOPBACK_TOKEN, encoding="utf-8")
    token.chmod(0o600)
    data = directory / "data"
    token_id = TokenRegistry(data).issue(fixture.PROJECT, "orchestrator").token_id
    started = datetime.now(UTC)
    try:
        assert not fixture.locked(data)
        async with fixture.stdio(config, token_id) as client:
            board = await fixture.snapshot(client, 1, started)
            assert board["github"]["available"] is True
            assert fixture.locked(data)
        assert set(github_fixture.callers) == {f"Bearer {LOOPBACK_TOKEN}"}
    finally:
        await fixture.stop_daemon(data)
