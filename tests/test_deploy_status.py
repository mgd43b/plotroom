"""Local deployment observations and same-lane reports."""

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import pytest

from conftest import start_plotroom, tool_json, write_config
from fakes import FixedClock
from plotroom.adapters.github import LocalGitHubCredential
from plotroom.adapters.github.deployments import DEPLOYMENTS, poll_deployments
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.tokens import TokenRegistry
from plotroom.core.catalog import build_catalog
from plotroom.core.events import format_time
from plotroom.core.github import DETAILS
from plotroom.core.github_projection import MAIN
from plotroom.core.ledger import Ledger
from plotroom.core.reports import DEPLOY_STATUS
from test_lanes import lane_client

REPO = "fixture/repo"
API = "https://api.github.com"
SHA = "a" * 40


class DeploymentFixture:
    def __init__(self) -> None:
        self.status_failure = 503
        self.deployment_requests: list[tuple[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer fixture"
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        if path.startswith("/deployments"):
            page = request.url.params["page"]
            self.deployment_requests.append((path, page))
            if path == "/deployments":
                refs = {
                    "1": [(1, "refs/heads/release/deploy"), (2, "")],
                    "2": [(3, "refs/heads/shared"), (4, "refs/tags/release")],
                    "3": [(5, SHA)],
                }
                deployments = [
                    {"id": identifier, "sha": SHA, "environment": "Preview", "ref": ref}
                    for identifier, ref in refs[page]
                ]
                return self.page_response(path, deployments, int(page), 3)
            if path == "/deployments/1/statuses":
                if self.status_failure:
                    return httpx.Response(self.status_failure, headers={"Retry-After": "5"})
                return self.page_response(
                    path,
                    [{"id": int(page), "state": "success", "environment": "Staging Preview"}],
                    int(page),
                    3,
                )
            assert path in {
                "/deployments/2/statuses",
                "/deployments/3/statuses",
                "/deployments/4/statuses",
                "/deployments/5/statuses",
            }
            return httpx.Response(200, json=[{"id": 1, "state": "in_progress"}])
        if path == "":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/branches/main":
            return httpx.Response(200, json={"commit": {"sha": SHA}})
        if path == "/actions/runs":
            return httpx.Response(200, json={"workflow_runs": []})
        if path == f"/commits/{SHA}/check-runs":
            return httpx.Response(200, json={"check_runs": []})
        assert path in {"/issues", "/issues/comments"}, path
        return httpx.Response(200, json=[])

    @staticmethod
    def page_response(
        path: str, records: list[dict[str, Any]], page: int, last: int
    ) -> httpx.Response:
        headers = (
            {"Link": f'<{API}/repos/{REPO}{path}?page={page + 1}>; rel="next"'}
            if page < last
            else {}
        )
        return httpx.Response(200, json=records, headers=headers)


@pytest.fixture
async def deploy_store(tmp_path: Path) -> AsyncGenerator[SqliteStore]:
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    try:
        yield store
    finally:
        await store.close()


async def test_paginated_get_only_deployments_isolate_failures_and_deduplicate(
    deploy_store: SqliteStore,
) -> None:
    fixture = DeploymentFixture()
    clock = FixedClock()
    store = deploy_store
    branches = {
        "owner": "release/deploy",
        "shared-one": "shared",
        "shared-two": "shared",
        "tag": "refs/tags/release",
        "commit": SHA,
    }
    catalog = build_catalog(frozenset(branches), github_repository=REPO)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        ledger = Ledger(store, catalog, clock, github_repository=REPO)
        api = GitHubReadClient(API, REPO)
        credential = LocalGitHubCredential("TOKEN", {"TOKEN": "fixture"}, None)
        poller = GitHubDetailPoller(ledger, clock, api, credential)
        ledger.start()
        try:
            for worker, branch in branches.items():
                await ledger.execute(
                    "orchestrator",
                    "lane_start",
                    worker,
                    {"lane": worker, "worker": worker, "branch": branch, "work_items": []},
                )
            await poller.poll(client)
            first = await ledger.github_resource(REPO, MAIN)
            assert first is not None
            assert first.complete
            (progress,) = [
                event for event in await ledger.events_after(0) if event.kind == DEPLOY_STATUS
            ]
            assert progress.fields["deployment_id"] == 2
            assert progress.fields["finished"] is False
            cursor = await ledger.fetch_cursor(REPO, DEPLOYMENTS)
            assert cursor is not None
            assert cursor.watermark == "2:0"

            fixture.status_failure = 429
            await poller.poll(client)
            assert await ledger.fetch_retry(REPO, DETAILS) is not None
            assert await ledger.fetch_cursor(REPO, DEPLOYMENTS) == cursor
            denied_at = len(fixture.deployment_requests)
            await poller.poll(client)
            assert len(fixture.deployment_requests) == denied_at

            clock.advance(5)
            fixture.status_failure = 0
            await poller.poll(client)
            assert await ledger.fetch_retry(REPO, DETAILS) is None
            cursor = await ledger.fetch_cursor(REPO, DEPLOYMENTS)
            assert cursor is not None
            assert cursor.watermark == "3:0"
            status_cursor = await ledger.fetch_cursor(REPO, "deployment_statuses/1")
            assert status_cursor is not None
            assert status_cursor.watermark == "3"
        finally:
            await ledger.stop()

        ledger = Ledger(store, catalog, clock, github_repository=REPO)
        poller = GitHubDetailPoller(ledger, clock, api, credential)
        ledger.start()
        try:
            await poller.poll(client)
            events = [
                event for event in await ledger.events_after(0) if event.kind == DEPLOY_STATUS
            ]
        finally:
            await ledger.stop()
    assert fixture.deployment_requests[-6:] == [
        ("/deployments", "1"),
        ("/deployments", "3"),
        ("/deployments/1/statuses", "1"),
        ("/deployments/1/statuses", "3"),
        ("/deployments/2/statuses", "1"),
        ("/deployments/5/statuses", "1"),
    ]
    assert [(event.fields["deployment_id"], event.fields["status_id"]) for event in events] == [
        (2, 1),
        (1, 1),
        (1, 2),
        (3, 1),
        (4, 1),
        (1, 3),
        (5, 1),
    ]
    assert events[0].id == progress.id
    assert events[0].fields["environment"] == "Preview"
    assert events[1].fields == {
        "repository": REPO,
        "deployment_id": 1,
        "status_id": 1,
        "environment": "Staging Preview",
        "sha": SHA,
        "state": "success",
        "finished": True,
    }
    assert all(event.source == "deploy" for event in events)
    assert {event.fields["deployment_id"]: event.lane_id for event in events} == {
        1: "owner",
        2: None,
        3: None,
        4: None,
        5: None,
    }


async def test_deployment_history_sweep_keeps_latest_status_fresh(
    deploy_store: SqliteStore,
) -> None:
    last = 34
    requests: list[tuple[str, int]] = []
    newest_status = 0

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer fixture"
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        page = int(request.url.params["page"])
        requests.append((path, page))
        if path == "/deployments":
            ids = range(1001, 1013) if page == 1 else (page,)
            rows = [{"id": identifier, "sha": SHA, "environment": "Preview"} for identifier in ids]
            return DeploymentFixture.page_response(path, rows, page, last)
        assert path.startswith("/deployments/")
        assert path.endswith("/statuses")
        assert page == 1
        status = newest_status if path == "/deployments/1001/statuses" else 1
        return httpx.Response(200, json=[{"id": status, "state": "success"}])

    clock = FixedClock()
    api = GitHubReadClient(API, REPO)
    ledger = Ledger(deploy_store, build_catalog(github_repository=REPO), clock)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        ledger.start()
        try:
            for newest_status in range(1, last + 2):
                since = await ledger.head_seq()
                requests.clear()
                await poll_deployments(ledger, api, client, "fixture", format_time(clock.now()))
                assert 1 <= len(requests) <= 12
                assert ("/deployments", 1) in requests
                assert ("/deployments/1001/statuses", 1) in requests
                newest = [
                    event.fields["status_id"]
                    for event in await ledger.events_after(since)
                    if event.kind == DEPLOY_STATUS and event.fields["deployment_id"] == 1001
                ]
                assert newest == [newest_status]
            assert ("/deployments", last) in requests
            assert (f"/deployments/{last}/statuses", 1) in requests
            assert any(
                event.kind == DEPLOY_STATUS and event.fields["deployment_id"] == last
                for event in await ledger.events_after(0)
            )
        finally:
            await ledger.stop()


@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_only_own_lane_worker_can_report_and_replay(
    transport: str, tmp_path: Path, token_home: Path
) -> None:
    config = write_config(tmp_path)
    config.write_text(
        config.read_text() + "\n[projects.demo.workers.owner]\n[projects.demo.workers.foreign]\n",
        encoding="utf-8",
    )
    tokens = TokenRegistry(tmp_path / "data")
    orch = tokens.issue("demo", "orchestrator", local=True)
    owner = tokens.issue("demo", "worker:owner", local=True)
    foreign = tokens.issue("demo", "worker:foreign", local=True)
    daemon = start_plotroom("serve", "--config", str(config))
    args = {"lane": "deploy", "environment": "Staging Preview", "sha": SHA, "state": "success"}
    try:
        port = daemon.wait_for_log("serving")["port"]
        url = f"http://127.0.0.1:{port}/mcp"
        async with (
            lane_client(transport, config, url, orch.bearer, orch.token_id) as orchestrator,
            lane_client(transport, config, url, owner.bearer, owner.token_id) as worker,
            lane_client(transport, config, url, foreign.bearer, foreign.token_id) as other,
        ):
            started = tool_json(
                await orchestrator.call_tool(
                    "lane_start",
                    {
                        "lane": "deploy",
                        "worker": "owner",
                        "branch": "deploy",
                        "work_items": [],
                        "request_id": "start",
                    },
                )
            )
            assert started["ok"]
            for caller in (orchestrator, other):
                denied = tool_json(
                    await caller.call_tool("deploy_report", {**args, "request_id": "one"})
                )
                assert denied["error"]["code"] == "forbidden"

            one = tool_json(await worker.call_tool("deploy_report", {**args, "request_id": "one"}))
            repeat = tool_json(
                await worker.call_tool("deploy_report", {**args, "request_id": "one"})
            )
            two = tool_json(await worker.call_tool("deploy_report", {**args, "request_id": "two"}))
            assert one == repeat
            assert one["data"]["created"] is True
            assert two["data"] == {"event": one["data"]["event"], "created": False}
            for field, value in (("environment", " "), ("sha", SHA[:8]), ("state", "unknown")):
                invalid = tool_json(
                    await worker.call_tool(
                        "deploy_report", {**args, field: value, "request_id": field}
                    )
                )
                assert invalid["error"]["code"] == "invalid"

            archived = tool_json(
                await orchestrator.call_tool(
                    "lane_archive", {"lane": "deploy", "request_id": "archive"}
                )
            )
            assert archived["ok"]
            denied = tool_json(
                await worker.call_tool("deploy_report", {**args, "request_id": "late"})
            )
            assert denied["error"]["code"] == "conflict"
    finally:
        daemon.stop()

    store = SqliteStore("demo", tmp_path / "data")
    try:
        (event,) = [
            event for event in await store.events_after(0, 100) if event.kind == DEPLOY_STATUS
        ]
    finally:
        await store.close()
    assert event.id == one["data"]["event"]
    assert event.lane_id == "deploy"
    assert event.fields == {
        "environment": "Staging Preview",
        "sha": SHA,
        "state": "success",
        "finished": True,
    }
