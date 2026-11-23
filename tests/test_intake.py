import asyncio
import json
import sys
from pathlib import Path

import pytest

from conftest import authenticated_client, child_env, tool_json, write_config
from plotroom.app.config import load_config
from plotroom.app.serve import Daemon
from plotroom.app.tokens import TokenRegistry


@pytest.mark.parametrize("turn_index", [None, 2])
async def test_worker_ingest_wakes_local_watch_and_ack_closes(
    tmp_path: Path, token_home: Path, turn_index: int | None
) -> None:
    config = write_config(tmp_path)
    config.write_text(
        config.read_text()
        + '\n[projects.demo.session_reader]\nclaude_home = "unused-claude"\n'
        + 'app_support = "unused-desktop"\n[projects.demo.workers.fixture]\n'
        + 'claude_session = "fixture-session"\n[projects.demo.workers.other]\n'
    )
    settings = load_config(config, {})
    tokens = TokenRegistry(settings.data_dir)
    operator = tokens.issue("demo", "orchestrator", local=True)
    worker = tokens.issue("demo", "worker:fixture", local=True)
    other = tokens.issue("demo", "worker:other", local=True)
    daemon = Daemon(settings)
    serving = asyncio.create_task(daemon.run())
    try:
        async with asyncio.timeout(10):
            port = await daemon.wait_started()
        ledger = daemon.ledgers["demo"]
        url = f"http://127.0.0.1:{port}/mcp"
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "start",
            {"lane": "owned", "worker": "fixture", "branch": "demo", "work_items": []},
        )
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "other",
            {"lane": "unowned", "worker": "other", "branch": "other", "work_items": []},
        )
        watcher = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "plotroom",
            "watch",
            "--config",
            str(config),
            "--token-id",
            operator.token_id,
            "--cursor",
            str(await ledger.head_seq()),
            env=child_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            args = {
                "kind": "claude.turn_report",
                "session_id": "fixture-session",
                "body": "worker report",
                "lane": "unowned",
            }
            async with authenticated_client(url, other.bearer) as wrong_worker:
                denied = tool_json(await wrong_worker.call_tool("ingest", args))
                assert denied["error"]["code"] == "forbidden"
                assert "bound to another worker" in denied["error"]["message"]
            async with authenticated_client(url, worker.bearer) as client:
                denied = tool_json(
                    await client.call_tool("ingest", {**args, "kind": "github.pr_opened"})
                )
                assert denied["error"]["code"] == "forbidden"
                payload = {**args, "turn_index": turn_index}
                made = tool_json(await client.call_tool("ingest", payload))["data"]
                assert made["created"]
                same = tool_json(await client.call_tool("ingest", payload))["data"]
                assert same == {"event": made["event"], "created": False}
                other_route = tool_json(
                    await client.call_tool(
                        "ingest", {**args, "turn_index": 2 if turn_index is None else None}
                    )
                )["data"]
                assert other_route == same
            async with asyncio.timeout(10):
                stdout, stderr = await watcher.communicate()
            assert watcher.returncode == 0, stderr.decode()
            item = json.loads(stdout)["item"]
            assert item["kind"] == "inbound_message"
            assert item["context"]["lane_id"] == "owned"
            assert item["context"]["body"] == "worker report"
            reports = [event for event in await ledger.events_after(0) if event.source == "claude"]
            assert len(reports) == 1
            assert reports[0].lane_id == "owned"
            async with authenticated_client(url, operator.bearer) as client:
                assert (
                    tool_json(
                        await client.call_tool(
                            "ack", {"item": item["id"], "request_id": "ack-turn"}
                        )
                    )["data"]["state"]
                    == "closed"
                )
            assert not (await ledger.read_board("orchestrator"))[1]
        finally:
            if watcher.returncode is None:
                watcher.kill()
                await watcher.wait()
    finally:
        daemon.stop()
        async with asyncio.timeout(10):
            await serving
