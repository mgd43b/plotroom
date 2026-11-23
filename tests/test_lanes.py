import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import authenticated_client, start_plotroom, tool_json, write_config
from fakes import MemoryStore
from plotroom.app.tokens import TokenRegistry
from plotroom.core.github import PullRequest
from plotroom.core.lanes import Lane
from plotroom.core.merge_lane import merge_lane_evidence


@asynccontextmanager
async def lane_client(
    transport: str, config: Path, url: str, bearer: str, token_id: str
) -> AsyncGenerator[Client]:
    if transport == "http":
        async with authenticated_client(url, bearer) as client:
            yield client
    else:
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
                token_id,
            ],
        )
        async with Client(proxy, mode="legacy") as client:
            yield client


@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_local_lane_smoke(transport: str, tmp_path: Path, token_home: Path) -> None:
    config = write_config(tmp_path)
    config.write_text(config.read_text() + "\n[projects.demo.workers.fixture]\n", encoding="utf-8")
    tokens = TokenRegistry(tmp_path / "data")
    orch = tokens.issue("demo", "orchestrator", local=True)
    worker = tokens.issue("demo", "worker:fixture", local=True)
    first = start_plotroom("serve", "--config", str(config))
    try:
        port = first.wait_for_log("serving")["port"]
        url = f"http://127.0.0.1:{port}/mcp"
        args = {
            "lane": "demo-lane",
            "request_id": "start",
            "worker": "fixture",
            "work_items": ["fixture.1"],
            "branch": "demo",
            "branch_pattern": "demo*",
        }
        async with (
            lane_client(transport, config, url, orch.bearer, orch.token_id) as orchestrator,
            lane_client(transport, config, url, worker.bearer, worker.token_id) as worker_client,
        ):
            denied = tool_json(await worker_client.call_tool("lane_start", args))
            assert denied["error"]["code"] == "forbidden"
            started = tool_json(await orchestrator.call_tool("lane_start", args))
            assert started["data"]["worker"] == "fixture"
            assert started["data"]["branch_pattern"] == "demo*"
            update = tool_json(
                await worker_client.call_tool(
                    "lane_update",
                    {"lane": "demo-lane", "waiting_on": "orchestrator", "request_id": "waiting"},
                )
            )
            assert update["data"]["waiting_on"] == "orchestrator"
            item = tool_json(await orchestrator.call_tool("next", {}))["data"]["items"][0]
            assert item["kind"] == "lane_waiting"
            waiting = tool_json(await orchestrator.call_tool("snapshot", {}))["data"]
            assert waiting["items"] == [item]
            assert waiting["lanes"] == {"active": 1}
            assert tool_json(await worker_client.call_tool("next", {}))["data"]["items"] == []
            ack_args = {"item": item["id"], "request_id": "ack-first"}
            acked = tool_json(await orchestrator.call_tool("ack", ack_args))["data"]
            assert acked["state"] == "snoozed"
            assert acked["changed"] is True
            before = tool_json(await orchestrator.call_tool("snapshot", {}))
            again = tool_json(
                await orchestrator.call_tool("ack", {**ack_args, "request_id": "ack-repeat"})
            )["data"]
            assert again["changed"] is False
            assert tool_json(await orchestrator.call_tool("snapshot", {})) == before
            next_before = tool_json(await orchestrator.call_tool("next", {}))
    finally:
        first.stop()

    second = start_plotroom("serve", "--config", str(config))
    try:
        port = second.wait_for_log("serving")["port"]
        url = f"http://127.0.0.1:{port}/mcp"
        async with lane_client(transport, config, url, orch.bearer, orch.token_id) as orchestrator:
            assert tool_json(await orchestrator.call_tool("snapshot", {})) == before
            assert tool_json(await orchestrator.call_tool("next", {})) == next_before
            ready = tool_json(
                await orchestrator.call_tool(
                    "lane_ready",
                    {"lane": "demo-lane", "pr": 1, "head_sha": "a" * 40, "request_id": "ready"},
                )
            )
            assert ready["data"]["state"] == "ready"
            assert ready["data"]["work_items"] == ["fixture.1"]
            board = tool_json(await orchestrator.call_tool("snapshot", {}))["data"]
            assert board["lane_details"] == [ready["data"]]
            assert before["data"]["lane_details"][0]["waiting_on"] == "orchestrator"
            assert before["data"]["items"] == []
            archived = tool_json(
                await orchestrator.call_tool(
                    "lane_archive", {"lane": "demo-lane", "request_id": "archive"}
                )
            )
            assert archived["data"]["state"] == "archived"
    finally:
        second.stop()


async def test_memory_board_matches_lane_visibility_and_counts() -> None:
    store = MemoryStore()
    active = Lane("active", "fixture", (), "demo")
    archived = Lane("archived", "other", (), "closed", state="archived")
    async with store.transaction() as tx:
        await tx.save_lane(active)
        await tx.save_lane(archived)
    assert (await store.read_board("orchestrator"))[2:] == ({"active": 1, "archived": 1}, [active])
    assert (await store.read_board("worker:fixture"))[2:] == ({"active": 1}, [active])
    assert (await store.read_board("worker:other"))[2:] == ({"archived": 1}, [])


async def test_projected_branch_identity_uses_literal_fallback_and_unique_live_match() -> None:
    store = MemoryStore()
    repo = "fixture/project"
    stamp = "2026-11-23T12:00:00Z"
    pull = PullRequest(repo, 7, "work/b", "a" * 40, repo, "open", stamp, stamp, True, 1)
    literal = Lane("literal", "worker1", (), "work/[b]")
    pattern = Lane("pattern", "worker2", (), "work/a", branch_pattern="work/[ab]")
    archived = Lane("archived", "worker3", (), "work/b", state="archived")
    async with store.transaction() as tx:
        await tx.save_lane(literal)
        await tx.save_lane(archived)
        assert await merge_lane_evidence(tx, pull) == (None, None, False)
        await tx.save_lane(pattern)
        assert await merge_lane_evidence(tx, pull) == (pattern, None, False)
        await tx.save_lane(Lane("overlap", "worker4", (), "work/b"))
        assert await merge_lane_evidence(tx, pull) == (None, None, True)
