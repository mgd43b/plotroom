import asyncio
import json
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import child_env, tool_json, write_config
from fakes import FixedClock
from plotroom.adapters.github import GitHubPoller, LocalGitHubCredential, PullRequestFetcher
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.tokens import TokenRegistry
from plotroom.core.catalog import build_catalog
from plotroom.core.github import PR_MERGED, PR_OPENED, PULL_REQUESTS
from plotroom.core.ledger import Ledger
from test_daemon_start import stop_owner

REPO = "fixture/read-only"
SECRET = "PLOTROOM_DEMO_GITHUB_TOKEN"
TOKEN = "fixture-token"
HEAD = "a" * 40


def pull(*, state: str = "open", merged: bool = False, seconds: int = 0) -> dict[str, Any]:
    stamp = f"2026-11-22T12:00:{seconds:02d}Z"
    return {
        "number": 12,
        "state": state,
        "merged_at": stamp if merged else None,
        "updated_at": stamp,
        "head": {"ref": "fixture-branch", "sha": HEAD},
        "body": "fixture pull request",
    }


@dataclass
class FixtureGitHub:
    url: str = ""
    rows: list[dict[str, Any]] = field(default_factory=lambda: [pull()])
    callers: list[str] = field(default_factory=lambda: list[str]())
    status: int = 200


@pytest.fixture
def github_fixture() -> Iterator[FixtureGitHub]:
    data = FixtureGitHub()

    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            auth = self.headers.get("Authorization", "")
            data.callers.append(auth)
            if urlsplit(self.path).path != f"/repos/{REPO}/pulls":
                self.send_error(404)
                return
            if auth != f"Bearer {TOKEN}":
                self.send_error(401)
                return
            payload = json.dumps(data.rows).encode()
            self.send_response(data.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
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
