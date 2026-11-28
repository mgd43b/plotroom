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

from conftest import authenticated_client, child_env, start_plotroom, tool_json, write_config
from fakes import FixedClock
from plotroom.adapters.github import GitHubPoller, LocalGitHubCredential, PullRequestFetcher
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.config import load_config
from plotroom.app.serve import Daemon
from plotroom.app.tokens import TokenRegistry
from plotroom.core import ledger as ledger_module
from plotroom.core.catalog import build_catalog
from plotroom.core.events import LedgerError, format_time
from plotroom.core.github import (
    PR_CLOSED,
    PR_HEAD_CHANGED,
    PR_MERGED,
    PR_OPENED,
    PR_REOPENED,
    PULL_REQUESTS,
    FetchCursor,
    ObservedPull,
)
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork
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


@pytest.mark.parametrize("first_empty", [False, True])
async def test_mcp_snapshot_captures_github_health_with_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_empty: bool
) -> None:
    config = write_config(tmp_path, projects=("demo", "plain"))
    config.write_text(
        config.read_text()
        + f'\n[projects.demo.github]\nrepository = "{REPO}"\n'
        + 'api_url = "http://127.0.0.1:9"\n'
    )
    settings = load_config(config, {})
    tokens = TokenRegistry(settings.data_dir)
    operator = tokens.issue("demo", "orchestrator", local=False)
    plain = tokens.issue("plain", "orchestrator", local=False)
    clock = FixedClock()
    daemon = Daemon(settings, clock=clock)

    async def park_polling(_self: GitHubPoller, _after_pull: object = None) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(GitHubPoller, "run", park_polling)
    serving = asyncio.create_task(daemon.run())
    released = asyncio.Event()
    captured = asyncio.Event()
    try:
        async with asyncio.timeout(10):
            port = await daemon.wait_started()
        ledger = daemon.ledgers["demo"]
        current_rows = [] if first_empty else [pull()]
        status = 200

        def read(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=current_rows)

        poller = GitHubPoller(
            ledger,
            clock,
            REPO,
            LocalGitHubCredential(SECRET, {SECRET: TOKEN}, None),
            PullRequestFetcher("http://127.0.0.1:9", REPO),
            1,
        )
        url = f"http://127.0.0.1:{port}/mcp"
        async with authenticated_client(url, plain.bearer) as client:
            unconfigured = tool_json(await client.call_tool("snapshot"))["data"]
            assert "github" not in unconfigured
            assert "open_prs" not in unconfigured

        async with (
            authenticated_client(url, operator.bearer) as client,
            httpx.AsyncClient(transport=httpx.MockTransport(read), trust_env=False) as http,
        ):
            startup = tool_json(await client.call_tool("snapshot"))["data"]
            assert startup["open_prs"] == 0
            assert startup["github"] == {
                "available": False,
                "as_of": None,
                "error": "waiting for the first GitHub poll",
            }

            snapshot_observation = Ledger.snapshot_observation

            async def pause_after_capture(
                self: Ledger, role: str, *, checklist_lane: str | None = None
            ):
                observation = await snapshot_observation(self, role, checklist_lane=checklist_lane)
                if self is ledger and not captured.is_set():
                    captured.set()
                    await released.wait()
                return observation

            monkeypatch.setattr(Ledger, "snapshot_observation", pause_after_capture)
            # The real tool is paused after capture but before rendering. A
            # full production poll must be able to commit before render resumes.
            async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
                straddling = tasks.create_task(client.call_tool("snapshot"))
                try:
                    await captured.wait()
                    assert await poller.poll(http)
                finally:
                    released.set()

            first = tool_json(straddling.result())["data"]
            assert first["github"] == startup["github"]
            assert first["open_prs"] == startup["open_prs"]
            synced = tool_json(await client.call_tool("snapshot"))["data"]
            assert synced["github"] == {"available": True, "as_of": format_time(clock.now())}
            assert synced["open_prs"] == (0 if first_empty else 1)

            status = 401
            assert not await poller.poll(http)
            failed = tool_json(await client.call_tool("snapshot"))["data"]
            assert failed["github"] == {
                "available": False,
                "as_of": synced["github"]["as_of"],
                "error": "GitHub denied access to the configured repository",
            }
            assert failed["open_prs"] == synced["open_prs"]

            status = 200
            clock.advance(1)
            current_rows = [pull(state="open" if first_empty else "closed", seconds=1)]
            assert await poller.poll(http)
            recovered = tool_json(await client.call_tool("snapshot"))["data"]
            assert recovered["github"] == {"available": True, "as_of": format_time(clock.now())}
            assert recovered["open_prs"] == (1 if first_empty else 0)
    finally:
        released.set()
        daemon.stop()
        async with asyncio.timeout(10):
            await serving


async def test_snapshot_can_read_while_canceled_poll_is_queued(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    ledger = Ledger(store, build_catalog(), clock, github_repository=REPO)
    ledger.start()
    notification = ledger.commit_signal
    writing = asyncio.Event()
    release = asyncio.Event()
    attempted = asyncio.Event()

    async def hold_writer(_uow: UnitOfWork) -> None:
        writing.set()
        await release.wait()

    async def poll() -> bool:
        attempted.set()
        return await ledger.commit_github_pull_poll(REPO, None, (), format_time(clock.now()))

    try:
        async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
            tasks.create_task(ledger.run("system", hold_writer))
            try:
                await writing.wait()
                polling = tasks.create_task(poll())
                await attempted.wait()
                observation = await ledger.snapshot_observation("orchestrator")
                assert observation[1] is not None
                assert (observation[1].open_prs, observation[1].as_of) == (0, None)
                assert observation[-1] == "waiting for the first GitHub poll"
                assert not notification.is_set()
                polling.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await polling
            finally:
                release.set()
        assert await ledger.run("system", lambda uow: uow.fetch_cursor(REPO, PULL_REQUESTS)) is None
    finally:
        await ledger.stop()
        await store.close()


async def test_snapshot_health_is_local_to_the_running_ledger(tmp_path: Path) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    previous = Ledger(store, build_catalog(), clock, github_repository=REPO)
    reopened = Ledger(store, build_catalog(), clock, github_repository=REPO)
    previous.start()
    try:
        as_of = format_time(clock.now())
        assert await previous.commit_github_pull_poll(REPO, None, (), as_of)
        assert (await previous.snapshot_observation("orchestrator"))[-1] is None
        await previous.stop()

        reopened.start()
        startup = await reopened.snapshot_observation("orchestrator")
        assert startup[1] is not None
        assert startup[1].as_of == as_of
        assert startup[-1] == "waiting for the first GitHub poll"
        notification = reopened.commit_signal
        await reopened.set_github_error("GitHub fixture unavailable")
        failed = await reopened.snapshot_observation("orchestrator")
        assert failed[1] is not None
        assert (failed[1].open_prs, failed[1].as_of) == (0, as_of)
        assert failed[-1] == "GitHub fixture unavailable"
        assert reopened.commit_signal is notification
        assert not notification.is_set()
    finally:
        await previous.stop()
        await reopened.stop()
        await store.close()


@pytest.fixture
def held_pr_publication(monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    writing = asyncio.Event()
    release = asyncio.Event()
    apply = ledger_module.apply_pull_poll

    async def pause_before_commit(
        uow: UnitOfWork,
        repository: str,
        baseline: FetchCursor | None,
        pulls: tuple[ObservedPull, ...],
        as_of: str,
    ) -> bool:
        result = await apply(uow, repository, baseline, pulls, as_of)
        writing.set()
        await release.wait()
        return result

    monkeypatch.setattr(ledger_module, "apply_pull_poll", pause_before_commit)
    return writing, release


async def test_successful_poll_does_not_clear_newer_provider_error(
    tmp_path: Path, held_pr_publication: tuple[asyncio.Event, asyncio.Event]
) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    ledger = Ledger(store, build_catalog(), clock, github_repository=REPO)
    ledger.start()
    writing, release = held_pr_publication
    error_started = asyncio.Event()

    async def report_error() -> None:
        error_started.set()
        await ledger.set_github_error("newer provider error")

    try:
        async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
            poll = tasks.create_task(
                ledger.commit_github_pull_poll(REPO, None, (), format_time(clock.now()))
            )
            try:
                await writing.wait()
                tasks.create_task(report_error())
                await error_started.wait()
                assert ledger.github_error == "newer provider error"
            finally:
                release.set()
        assert poll.result()
        assert ledger.github_error == "newer provider error"
        assert (await ledger.snapshot_observation("orchestrator"))[-1] == ledger.github_error
    finally:
        await ledger.stop()
        await store.close()


async def test_canceling_active_poll_does_not_split_committed_health(
    tmp_path: Path, held_pr_publication: tuple[asyncio.Event, asyncio.Event]
) -> None:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    ledger = Ledger(store, build_catalog(), clock, github_repository=REPO)
    ledger.start()
    writing, release = held_pr_publication
    as_of = format_time(clock.now())
    try:
        async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
            poll = tasks.create_task(ledger.commit_github_pull_poll(REPO, None, (), as_of))
            try:
                await writing.wait()
                poll.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await poll
                before = await ledger.snapshot_observation("orchestrator")
                assert before[1] is not None
                assert before[1].as_of is None
                assert before[-1] == "waiting for the first GitHub poll"
            finally:
                release.set()
            cursor = await ledger.run("system", lambda uow: uow.fetch_cursor(REPO, PULL_REQUESTS))
            assert cursor is not None
            assert cursor.as_of == as_of
        after = await ledger.snapshot_observation("orchestrator")
        assert after[1] is not None
        assert after[1].as_of == as_of
        assert after[-1] is ledger.github_error is None
    finally:
        await ledger.stop()
        await store.close()


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
