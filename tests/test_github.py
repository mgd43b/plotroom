import asyncio
import json
import sys
import threading
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import child_env, start_plotroom, tool_json, write_config
from fakes import FixedClock
from plotroom.adapters.github import GitHubPoller, LocalGitHubCredential, PullRequestFetcher
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.tokens import TokenRegistry
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError, format_time
from plotroom.core.github import (
    PR_CLOSED,
    PR_HEAD_CHANGED,
    PR_MERGED,
    PR_OPENED,
    PR_REOPENED,
    PULL_REQUESTS,
)
from plotroom.core.ledger import Ledger
from test_daemon_start import stop_owner

REPO = "fixture/read-only"
SECRET = "PLOTROOM_DEMO_GITHUB_TOKEN"
TOKEN = "fixture-token"
HEAD = "a" * 40


def pull(
    *,
    state: str = "open",
    merged: bool = False,
    seconds: int = 0,
    head: str = HEAD,
    number: int = 12,
    head_repository: str = REPO,
) -> dict[str, Any]:
    stamp = f"2026-11-22T12:00:{seconds:02d}Z"
    return {
        "number": number,
        "state": state,
        "merged_at": stamp if merged else None,
        "updated_at": stamp,
        "head": {"ref": "fixture-branch", "sha": head, "repo": {"full_name": head_repository}},
        "body": "fixture pull request",
    }


@dataclass
class FixtureGitHub:
    url: str = ""
    rows: list[dict[str, Any]] = field(default_factory=lambda: [pull()])
    callers: list[str] = field(default_factory=lambda: list[str]())
    status: int = 200
    error_message: str | None = None
    headers: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    page_two: list[dict[str, Any]] | None = None
    page_two_entered: threading.Event = field(default_factory=threading.Event)
    release_page_two: threading.Event | None = None


@pytest.fixture
def github_fixture() -> Iterator[FixtureGitHub]:
    data = FixtureGitHub()

    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            auth = self.headers.get("Authorization", "")
            data.callers.append(auth)
            request = urlsplit(self.path)
            if request.path != f"/repos/{REPO}/pulls":
                self.send_error(404)
                return
            if auth != f"Bearer {TOKEN}":
                self.send_error(401)
                return
            second = parse_qs(request.query).get("page") == ["2"]
            if second:
                data.page_two_entered.set()
                if (release := data.release_page_two) is not None and not release.wait(20):
                    return
            content = data.page_two if second else data.rows
            payload = json.dumps(
                {"message": data.error_message} if data.error_message is not None else content
            ).encode()
            self.send_response(data.status)
            if data.page_two is not None and not second:
                self.send_header("Link", f'<{data.url}/repositories/123/pulls?page=2>; rel="next"')
            for key, value in data.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            # The restart test kills its client during this response.
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler) as server:
        data.url = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield data
        finally:
            server.shutdown()
            thread.join()


async def test_pull_lifecycle_and_credentials_preserve_committed_state(
    tmp_path: Path, github_fixture: FixtureGitHub
) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    workers = frozenset({"fixture"})
    ledger = Ledger(store, build_catalog(workers), clock, github_repository=REPO)
    ledger.start()
    env: dict[str, str] = {}
    credential = LocalGitHubCredential(SECRET, env, None)
    poller = GitHubPoller(
        ledger, clock, REPO, credential, PullRequestFetcher(github_fixture.url, REPO), 1
    )
    try:
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "start",
            {
                "lane": "fixture-lane",
                "worker": "fixture",
                "branch": "fixture-branch",
                "work_items": [],
            },
        )
        async with httpx.AsyncClient(trust_env=False) as client:
            assert not await poller.poll(client)
            assert ledger.github_error == f"GitHub token missing ({SECRET})"
            assert await ledger.fetch_cursor(REPO, PULL_REQUESTS) is None
            assert not github_fixture.callers
            env[SECRET] = TOKEN
            assert await poller.poll(client)
            assert ledger.github_error is None
            _, summary = await ledger.snapshot_board("orchestrator")
            assert summary is not None
            assert summary.open_prs == 1
            baseline = await ledger.fetch_cursor(REPO, PULL_REQUESTS)
            github_fixture.rows = [pull(state="closed", merged=True, seconds=2)]
            github_fixture.status = 401
            assert not await poller.poll(client)
            assert ledger.github_error == "GitHub denied access to the configured repository"
            assert await ledger.fetch_cursor(REPO, PULL_REQUESTS) == baseline
            assert (await ledger.snapshot_board("orchestrator"))[1] == summary
            github_fixture.status = 200
            clock.advance(2)
            assert await poller.poll(client)
            assert await poller.poll(client)
            _, summary = await ledger.snapshot_board("orchestrator")
            assert summary is not None
            assert summary.open_prs == 0
        projected = await ledger.pull_request(REPO, 12)
        assert projected is not None
        assert projected.state == "merged"
        assert projected.head_sha == HEAD
        events = [event for event in await ledger.events_after(0) if event.source == "github"]
        assert [event.kind for event in events] == [PR_OPENED, PR_MERGED]
        assert all(
            event.lane_id == "fixture-lane" and event.body == "fixture pull request"
            for event in events
        )
        assert all(TOKEN not in str(event) for event in events)
    finally:
        await ledger.stop()
        await store.close()


async def test_on_demand_daemon_reads_the_mounted_pat(
    tmp_path: Path, token_home: Path, github_fixture: FixtureGitHub
) -> None:
    config = write_config(tmp_path)
    data_dir = tmp_path / "data"
    (tmp_path / "provider-token").write_text(TOKEN + "\n", encoding="utf-8")
    config.write_text(
        config.read_text()
        + f'\n[projects.demo.github]\nrepository = "{REPO}"\n'
        + f'api_url = "{github_fixture.url}"\npoll_seconds = 1\ntoken_file = "provider-token"\n'
    )
    local = TokenRegistry(data_dir).issue("demo", "orchestrator", local=True)
    proxy = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "plotroom",
            "connect",
            "--stdio",
            "--config",
            str(config),
            "--token-id",
            local.token_id,
        ],
        env={**child_env(), SECRET: "proxy-only-wrong-token"},
    )
    try:
        async with asyncio.timeout(15), Client(proxy, mode="legacy") as client:
            while True:
                board = tool_json(await client.call_tool("snapshot"))["data"]
                if board["github"]["available"]:
                    break
                await asyncio.sleep(0.05)
            assert board["open_prs"] == 1
            assert board["github"]["as_of"]
            assert set(github_fixture.callers) == {f"Bearer {TOKEN}"}
            github_fixture.rows = [pull(state="closed", merged=True, seconds=2)]
            while (board := tool_json(await client.call_tool("snapshot"))["data"])["open_prs"]:  # noqa: ASYNC110 - daemon subprocess
                await asyncio.sleep(0.05)
            assert board["github"]["available"]
    finally:
        await asyncio.to_thread(stop_owner, data_dir)


@pytest.mark.parametrize("limit", ["reset", "retry_after", "secondary"])
async def test_heads_close_reopen_and_rate_limit_resume(
    tmp_path: Path, github_fixture: FixtureGitHub, limit: str
) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    workers = frozenset({"fixture"})
    ledger = Ledger(
        store,
        build_catalog(workers, github_repository=REPO),
        clock,
        github_repository=REPO,
    )
    ledger.start()
    poller = GitHubPoller(
        ledger,
        clock,
        REPO,
        LocalGitHubCredential(SECRET, {SECRET: TOKEN}, None),
        PullRequestFetcher(github_fixture.url, REPO),
        1,
    )
    try:
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "start",
            {"lane": "lane", "worker": "fixture", "branch": "fixture-branch", "work_items": []},
        )
        args = {"lane": "lane", "pr": 12, "head_sha": HEAD}
        new_head = "b" * 40
        async with httpx.AsyncClient(trust_env=False) as client:
            assert await poller.poll(client)
            assert (await ledger.execute("worker:fixture", "lane_ready", "first", args)).result[
                "pr"
            ] == 12
            baseline = await ledger.fetch_cursor(REPO, PULL_REQUESTS)
            assert baseline is not None
            github_fixture.rows = [pull(head=new_head, seconds=1)]
            github_fixture.status = 429 if limit == "retry_after" else 403
            if limit == "retry_after":
                github_fixture.headers = {"Retry-After": "3"}
            elif limit == "reset":
                github_fixture.headers = {
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(clock.now().timestamp()) + 3),
                }
            else:
                github_fixture.error_message = "You have exceeded a secondary rate limit."
            assert not await poller.poll(client)
            assert ledger.github_error == "GitHub rate limit reached"
            assert await ledger.fetch_retry(REPO, PULL_REQUESTS) is not None
            poller = GitHubPoller(
                ledger,
                clock,
                REPO,
                LocalGitHubCredential(SECRET, {SECRET: TOKEN}, None),
                PullRequestFetcher(github_fixture.url, REPO),
                1,
            )
            previous_calls = len(github_fixture.callers)
            github_fixture.status = 200
            github_fixture.headers = {}
            github_fixture.error_message = None
            clock.advance(59 if limit == "secondary" else 2)
            assert not await poller.poll(client)
            assert len(github_fixture.callers) == previous_calls
            assert await ledger.fetch_cursor(REPO, PULL_REQUESTS) == baseline
            clock.advance(1)
            assert await poller.poll(client)
            assert ledger.github_error is None
            assert await ledger.fetch_retry(REPO, PULL_REQUESTS) is None
            projected_lane = (await ledger.snapshot_board("orchestrator"))[0][3][0]
            assert projected_lane.head_sha == new_head
            assert projected_lane.state == "active"
            assert (await ledger.read_board("orchestrator"))[3][0].state == "active"
            with pytest.raises(LedgerError, match="head must match"):
                await ledger.execute("worker:fixture", "lane_ready", "stale", args)
            ready = {**args, "head_sha": new_head}
            await ledger.execute("worker:fixture", "lane_ready", "confirm-new-head", ready)
            for state, open_count in [("closed", 0), ("open", 1), ("closed", 0), ("open", 1)]:
                # GitHub timestamps have second resolution. Each observed transition is distinct.
                github_fixture.rows = [pull(state=state, head=new_head, seconds=1)]
                clock.advance(1)
                assert await poller.poll(client)
                assert await poller.poll(client)
                board, summary = await ledger.snapshot_board("orchestrator")
                assert summary is not None
                assert summary.open_prs == open_count
                assert board[3][0].state == "active"
            projected = await ledger.pull_request(REPO, 12)
            assert projected is not None
            assert projected.complete
            assert projected.as_of == format_time(clock.now())
            assert projected.head_sha == new_head
        events = [event for event in await ledger.events_after(0) if event.source == "github"]
        assert [event.kind for event in events] == [
            PR_OPENED,
            PR_HEAD_CHANGED,
            PR_CLOSED,
            PR_REOPENED,
            PR_CLOSED,
            PR_REOPENED,
        ]
        assert len({event.upstream_id for event in events}) == len(events)
    finally:
        await ledger.stop()
        await store.close()


async def test_daemon_killed_mid_poll_replays_from_committed_cursor(
    tmp_path: Path, github_fixture: FixtureGitHub
) -> None:
    config = write_config(tmp_path)
    (tmp_path / "fixture-token").write_text(TOKEN)
    config.write_text(
        config.read_text()
        + f'\n[projects.demo.github]\nrepository = "{REPO}"\n'
        + f'api_url = "{github_fixture.url}"\npoll_seconds = 1\ntoken_file = "fixture-token"\n'
    )
    store = SqliteStore("demo", tmp_path / "data")
    first = start_plotroom("serve", "--config", str(config))
    try:
        await asyncio.to_thread(first.wait_for_log, "serving")
        async with asyncio.timeout(12):
            while (baseline := await store.fetch_cursor(REPO, PULL_REQUESTS)) is None:  # noqa: ASYNC110 - daemon subprocess
                await asyncio.sleep(0.02)
        github_fixture.rows = [pull(state="closed", seconds=1)]
        github_fixture.release_page_two = release = threading.Event()
        github_fixture.page_two = [pull(number=13, seconds=1)]
        assert await asyncio.to_thread(github_fixture.page_two_entered.wait, 10)
        first.process.kill()
        await asyncio.to_thread(first.process.wait, 10)
        release.set()
        assert await store.fetch_cursor(REPO, PULL_REQUESTS) == baseline
        assert [event.kind for event in await store.events_after(0)] == [PR_OPENED]
        restarted = start_plotroom("serve", "--config", str(config))
        try:
            await asyncio.to_thread(restarted.wait_for_log, "serving")
            async with asyncio.timeout(12):
                while len(await store.events_after(0)) < 3:  # noqa: ASYNC110 - daemon subprocess
                    await asyncio.sleep(0.02)
            cursor = await store.fetch_cursor(REPO, PULL_REQUESTS)
            assert cursor is not None
            assert cursor.generation > baseline.generation
            assert [event.kind for event in await store.events_after(0)] == [
                PR_OPENED,
                PR_CLOSED,
                PR_OPENED,
            ]
            assert (await store.read_github_board("orchestrator", REPO))[1].open_prs == 1
        finally:
            await asyncio.to_thread(restarted.stop)
    finally:
        if github_fixture.release_page_two is not None:
            github_fixture.release_page_two.set()
        await asyncio.to_thread(first.stop)
        await store.close()


async def test_forks_and_ambiguous_branches_do_not_become_lane_prs(
    tmp_path: Path, github_fixture: FixtureGitHub
) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    catalog = build_catalog(frozenset({"fixture"}), github_repository=REPO)
    ledger = Ledger(store, catalog, clock, github_repository=REPO)
    ledger.start()
    poller = GitHubPoller(
        ledger,
        clock,
        REPO,
        LocalGitHubCredential(SECRET, {SECRET: TOKEN}, None),
        PullRequestFetcher(github_fixture.url, REPO),
        1,
    )
    try:
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "start",
            {"lane": "lane", "worker": "fixture", "branch": "fixture-branch", "work_items": []},
        )
        async with httpx.AsyncClient(trust_env=False) as client:
            github_fixture.rows = [pull(), pull(number=13, head_repository="fork/read-only")]
            assert await poller.poll(client)
            board, summary = await ledger.snapshot_board("orchestrator")
            assert board[3][0].pr == 12
            assert summary is not None
            assert summary.open_prs == 2
            events = [e for e in await ledger.events_after(0) if e.kind == PR_OPENED]
            assert [event.lane_id for event in events] == ["lane", None]
            with pytest.raises(LedgerError, match="head must match"):
                await ledger.execute(
                    "worker:fixture",
                    "lane_ready",
                    "fork",
                    {"lane": "lane", "pr": 13, "head_sha": HEAD},
                )
            github_fixture.rows.append(pull(number=14))
            assert await poller.poll(client)
            assert (await ledger.snapshot_board("orchestrator"))[0][3][0].pr is None
            with pytest.raises(LedgerError, match="head must match"):
                await ledger.execute(
                    "worker:fixture",
                    "lane_ready",
                    "ambiguous",
                    {"lane": "lane", "pr": 12, "head_sha": HEAD},
                )
    finally:
        await ledger.stop()
        await store.close()


async def test_incremental_fetch_keeps_boundary_and_skips_older_pages(
    tmp_path: Path, github_fixture: FixtureGitHub
) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    ledger = Ledger(store, build_catalog(), clock)
    ledger.start()
    poller = GitHubPoller(
        ledger,
        clock,
        REPO,
        LocalGitHubCredential(SECRET, {SECRET: TOKEN}, None),
        PullRequestFetcher(github_fixture.url, REPO),
        1,
    )
    old = {**pull(number=20), "updated_at": "2026-11-22T11:00:00Z"}
    boundary = {**pull(number=21), "updated_at": "2026-11-22T11:55:00Z"}
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            github_fixture.rows = [pull(), old]
            github_fixture.page_two = []
            assert await poller.poll(client)
            assert github_fixture.page_two_entered.is_set()
            github_fixture.page_two_entered.clear()
            github_fixture.rows = [pull(), boundary, old]
            clock.advance(1)
            assert await poller.poll(client)
            assert not github_fixture.page_two_entered.is_set()
            assert await ledger.pull_request(REPO, 21) is not None
    finally:
        await ledger.stop()
        await store.close()
