"""Verify the bridge with a fresh Compose instance and a scripted local relay."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parent.parent
DB_QUERY = """
import json, sqlite3, sys
from contextlib import closing
with closing(sqlite3.connect("file:/data/bridge-fixture.db?mode=ro", uri=True)) as db:
    if sys.argv[1] == "event":
        print(db.execute("SELECT count(*) FROM events WHERE kind = ? AND upstream_id = ?",
                         ("switchboard.message", sys.argv[2])).fetchone()[0])
    else:
        row = db.execute("SELECT state FROM actions WHERE action_key = ?", (sys.argv[2],)).fetchone()
        print(json.dumps(row[0] if row else None))
"""


def run(command, payload=None, *, env=None):
    return subprocess.check_output(command, input=payload, text=True, env=env, timeout=180)


def marker(state: Path, name: str) -> None:
    deadline = time.monotonic() + 20
    while not (state / name).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"the fixture did not reach {name}")
        time.sleep(0.05)


@asynccontextmanager
async def connect(url: str, token: str):
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, trust_env=False) as http,
        Client(streamable_http_client(url, http_client=http), mode="auto") as client,
    ):
        yield client


async def tool(client, name, arguments):
    response = await client.call_tool(name, arguments)
    result = json.loads(response.content[0].text)
    assert result.get("ok"), name
    return result["data"]


async def seed(url, operator, board):
    async with connect(url, operator) as client:
        await tool(
            client,
            "lane_start",
            {
                "lane": "fixture-lane",
                "worker": "fixture",
                "branch": "fixture",
                "work_items": [],
                "request_id": "fixture-lane",
            },
        )
        denied = await client.call_tool(
            "bridge_receive",
            {
                "board": board,
                "message_id": 1,
                "sender": "fixture-peer",
                "body": "wrong role",
            },
        )
        assert json.loads(denied.content[0].text)["error"]["code"] == "forbidden"


async def queue_trigger(url, operator):
    async with connect(url, operator) as client:
        await tool(
            client,
            "trigger",
            {
                "predicate": {"message_from": {"worker": "fixture"}},
                "action": {"send": {"worker": "fixture", "text": "hosted fixture send"}},
                "request_id": "fixture-trigger",
            },
        )


def smoke(root: Path, name: str, tag: str) -> None:
    board = name + "-board"
    config = root / "plotroom.toml"
    config.write_text(
        f'[projects.bridge-fixture.switchboard]\nboard = "{board}"\nvia_bridge = true\n'
        '[projects.bridge-fixture.workers.fixture]\nswitchboard_address = "fixture-peer"\n'
    )
    config.chmod(0o644)
    overlay = root / "compose.json"
    overlay.write_text(
        json.dumps(
            {
                "services": {
                    "plotroom": {
                        "environment": {"PLOTROOM_CONFIG": "/fixture/plotroom.toml"},
                        "volumes": [f"{config}:/fixture/plotroom.toml:ro"],
                    }
                }
            }
        )
    )
    compose = [
        "docker",
        "compose",
        "-p",
        name,
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(overlay),
    ]
    env = {
        **os.environ,
        "PLOTROOM_IMAGE_TAG": tag,
        "PLOTROOM_HTTP_PORT": "0",
        "PLOTROOM_PROJECTS": "bridge-fixture",
    }
    volume = name + "_data"
    assert not run(["docker", "volume", "ls", "-q", "--filter", f"name=^{volume}$"]).strip()
    assert not run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={name}"]
    ).strip()
    state = root / "relay"
    state.mkdir()
    (state / "available").touch()
    (state / "bridge-fixture").touch()
    (state / "board").write_text(board)

    def command(*arguments):
        return run([*compose, *arguments], env=env)

    def token(role):
        issued = command(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "plotroom",
            "token",
            "issue",
            "--config",
            "/fixture/plotroom.toml",
            "--project",
            "bridge-fixture",
            "--role",
            role,
            "--no-local",
        )
        return json.loads(issued)["token"]

    def database(which, value):
        return command("exec", "-T", "plotroom", "python", "-c", DB_QUERY, which, value).strip()

    processes = []
    try:
        command("up", "--detach", "--wait", "--no-build")
        url = "http://" + command("port", "plotroom", "8765").strip() + "/mcp"
        operator, bridge_token = token("orchestrator"), token("bridge")
        asyncio.run(seed(url, operator, board))

        def launch(mode):
            bridge = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "plotroom",
                    "bridge",
                    "--url",
                    url,
                    "--board",
                    board,
                    "--mode",
                    mode,
                    "--relay-command",
                    sys.executable,
                    str(ROOT / "tests" / "switchboard_child.py"),
                    str(state),
                ],
                env={**os.environ, "PLOTROOM_TOKEN": bridge_token},
            )
            processes.append(bridge)
            return bridge

        first = launch("auto")
        marker(state, "interrupted")
        assert database("event", f"{board}:7") == "1"
        assert not (state / "acked").exists()
        first.kill()
        first.wait(timeout=10)

        launch("legacy")
        marker(state, "acked")
        assert (state / "acked").read_text() == "redelivered"
        assert database("event", f"{board}:7") == "1"
        asyncio.run(queue_trigger(url, operator))
        (state / "outbound-cause").touch()
        marker(state, "send-first")
        first_send = json.loads((state / "send-first").read_text())
        deadline = time.monotonic() + 15
        while json.loads(database("action", first_send["idempotency_key"])) != "done":
            if time.monotonic() >= deadline:
                raise TimeoutError("the server did not complete the bridge lease")
            time.sleep(0.1)
        assert (state / "send-requests.jsonl").read_text().count("\n") == 1
        assert first_send["to"] == "fixture-peer"
        assert json.loads(first_send["body"]) == {
            "action_key": first_send["idempotency_key"],
            "body": "hosted fixture send",
        }
        print(
            "Bridge smoke passed: durable commit before ack, one inbound event on redelivery, and one leased outbound send."
        )
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        if run(["docker", "volume", "ls", "-q", "--filter", f"name=^{volume}$"]).strip():
            label = run(
                [
                    "docker",
                    "volume",
                    "inspect",
                    "--format",
                    '{{ index .Labels "com.docker.compose.project" }}',
                    volume,
                ]
            ).strip()
            assert label == name
            command("down", "--volumes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-tag", required=True)
    parser.add_argument(
        "--project", required=True, help="unique name for a disposable Compose project"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="plotroom-bridge-") as directory:
        smoke(Path(directory), args.project, args.image_tag)


if __name__ == "__main__":
    main()
