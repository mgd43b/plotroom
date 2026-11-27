"""Verify Compose restart and bridge delivery with a fresh local fixture."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parent.parent
DB_QUERY = """
import json, sqlite3, sys
from contextlib import closing
with closing(sqlite3.connect("file:/data/bridge-fixture.db?mode=ro", uri=True)) as db:
    if sys.argv[1] == "event":
        rows = db.execute("SELECT lane_id FROM events WHERE kind = ? AND upstream_id = ?",
                          ("switchboard.message", sys.argv[2]))
        print(json.dumps([lane for (lane,) in rows]))
    elif sys.argv[1] == "claude":
        rows = db.execute("SELECT id, kind, lane_id FROM events WHERE source = ? ORDER BY seq",
                          ("claude",))
        print(json.dumps(list(rows)))
    elif sys.argv[1] == "decision":
        print(db.execute("SELECT count(*) FROM events WHERE kind = ? AND upstream_id = ?",
                         ("core.decide", sys.argv[2])).fetchone()[0])
    else:
        row = db.execute("SELECT state FROM actions WHERE action_key = ?",
                         (sys.argv[2],)).fetchone()
        print(json.dumps(row[0] if row else None))
"""


def run(command, payload=None, *, env=None):
    return subprocess.check_output(  # noqa: S603 - fixture command arguments, no shell
        command, input=payload, text=True, env=env, timeout=180
    )


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


async def tool(client, name, arguments, **options):
    response = await client.call_tool(name, arguments, **options)
    result = json.loads(response.content[0].text)
    assert result.get("ok"), name
    return result["data"]


async def verify_worker(url, operator, worker, database) -> None:
    assert urlsplit(url).hostname == "127.0.0.1"
    async with (
        connect(url, operator) as client,
        connect(url, operator) as observer,
        connect(url, worker) as ingress,
    ):
        cursor = (await tool(client, "snapshot", {}))["cursor"]
        watcher = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            "plotroom",
            "watch",
            "--url",
            url,
            "--cursor",
            str(cursor),
            env={"PLOTROOM_TOKEN": operator},
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        armed = asyncio.Event()

        async def progress(_elapsed, _cap, _message=None):
            armed.set()

        wait = asyncio.create_task(
            tool(observer, "wait", {"cursor": cursor, "timeout": 20}, progress_callback=progress)
        )
        try:
            async with asyncio.timeout(15):
                await armed.wait()
                assert not wait.done()
                assert watcher.returncode is None
                payload = {
                    "kind": "claude.turn_report",
                    "session_id": "compose-fixture-session",
                    "body": "Compose worker report",
                    "turn_index": 1,
                }
                report = await tool(ingress, "ingest", payload)
                event = report["event"]
                assert report["created"] is True
                assert await tool(ingress, "ingest", payload) == {"event": event, "created": False}

                observed = await wait
                stdout, stderr = await watcher.communicate()
                assert watcher.returncode == 0, stderr.decode()
                assert json.loads(stdout) == observed
                assert observed["cursor"] > cursor
                item = observed["item"]
                assert item["kind"] == "inbound_message"
                assert item["context"]["kind"] == payload["kind"]
                assert item["context"]["body"] == payload["body"]
                assert item["context"]["lane_id"] == "fixture-lane"
            assert (
                await tool(client, "ack", {"item": item["id"], "request_id": "ack-worker-report"})
            )["state"] == "closed"
            message = await tool(
                ingress,
                "ingest",
                {
                    "kind": "claude.session_message",
                    "session_id": "compose-fixture-session",
                    "upstream_id": "synthetic-peer-message",
                    "body": "Compose worker session message",
                },
            )
            assert message["created"] is True
            durable = await asyncio.to_thread(database, "claude", "")
            assert json.loads(durable) == [
                [event, "claude.turn_report", "fixture-lane"],
                [message["event"], "claude.session_message", "fixture-lane"],
            ]
        finally:
            wait.cancel()
            if watcher.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(watcher.pid, signal.SIGKILL)
                async with asyncio.timeout(5):
                    await watcher.communicate()
            await asyncio.gather(wait, return_exceptions=True)


async def check_project(url, operator, decision_id=None):
    ready_url = url.removesuffix("/mcp") + "/readyz/bridge-fixture"
    async with httpx2.AsyncClient(trust_env=False) as http:
        ready = await http.get(ready_url)
        ready.raise_for_status()
        assert ready.json() == {"status": "ready", "projects": {"bridge-fixture": "ready"}}

    async with connect(url, operator) as client:
        snapshot = await tool(client, "snapshot", {})
        assert snapshot["project"] == "bridge-fixture"
        if decision_id is None:
            created = await tool(
                client,
                "decide",
                {
                    "who": "Fixture operator",
                    "exact_words": "Compose restart fixture",
                    "where_ref": {"kind": "chat", "ref": "compose-fixture"},
                    "governs": [],
                    "settles": [],
                    "request_id": "compose-fixture-one",
                },
            )
            return created["decision_id"]

        decisions = await tool(client, "decisions", {})
        (decision,) = decisions["decisions"]
        assert decision["decision_id"] == decision_id
        assert decision["exact_words"] == "Compose restart fixture"


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
    image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", f"plotroom:{tag}"]).strip()
    board = name + "-board"
    config = root / "plotroom.toml"
    config.write_text(
        "[projects.bridge-fixture]\nwatchers = []\n"
        f'[projects.bridge-fixture.switchboard]\nboard = "{board}"\nvia_bridge = true\n'
        "[projects.bridge-fixture.session_reader]\n"
        'claude_home = "/fixture/unused-claude"\napp_support = "/fixture/unused-desktop"\n'
        '[projects.bridge-fixture.workers.fixture]\nswitchboard_address = "fixture-peer"\n'
        'claude_session = "compose-fixture-session"\n'
    )
    config.chmod(0o644)
    overlay = root / "compose.json"
    overlay.write_text(
        json.dumps(
            {
                "services": {
                    "plotroom": {
                        "image": image_id,
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
    assert not run(["docker", "network", "ls", "-q", "--filter", f"name=^{name}_default$"]).strip()
    assert not run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={name}"]
    ).strip()

    def command(*arguments):
        return run([*compose, *arguments], env=env)

    def token(role):
        issued = command(
            "run",
            "--rm",
            "--no-deps",
            "--pull",
            "never",
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

    try:
        command("up", "--detach", "--wait", "--no-build", "--pull", "never")
        container = command("ps", "-q", "plotroom").strip()
        assert container
        assert run(["docker", "inspect", "--format", "{{.Image}}", container]).strip() == image_id
        print(f"Compose fixture is running local image {image_id}.", flush=True)
        url = "http://" + command("port", "plotroom", "8765").strip() + "/mcp"
        operator, bridge_token = token("orchestrator"), token("bridge")
        decision_id = asyncio.run(check_project(url, operator))
        command("restart", "plotroom")
        command("up", "--detach", "--wait", "--no-build", "--no-recreate", "--pull", "never")
        assert command("ps", "-q", "plotroom").strip() == container
        url = "http://" + command("port", "plotroom", "8765").strip() + "/mcp"
        asyncio.run(check_project(url, operator, decision_id))
        assert database("decision", "orchestrator:compose-fixture-one") == "1"
        print("Compose restart passed: readiness, original bearer, and one durable event.")

        verify_bridge(root, board, url, operator, bridge_token, database)
        asyncio.run(verify_worker(url, operator, token("worker:fixture"), database))
        print(
            "Compose worker passed: one durable report on replay, session message, "
            "armed MCP wake, CLI cursor observation, and acknowledgment."
        )
    finally:
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


def verify_bridge(
    root: Path, board: str, url: str, operator: str, bridge_token: str, database
) -> None:
    state = root / "relay"
    state.mkdir()
    (state / "available").touch()
    (state / "bridge-fixture").touch()
    (state / "board").write_text(board)

    processes = []
    try:
        asyncio.run(seed(url, operator, board))

        def launch(mode):
            bridge = subprocess.Popen(  # noqa: S603 - fixed Python entry point, no shell
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
                    "-u",
                    str(ROOT / "tests" / "switchboard_child.py"),
                    str(state),
                ],
                env={**os.environ, "PLOTROOM_TOKEN": bridge_token},
            )
            processes.append(bridge)
            return bridge

        first = launch("auto")
        marker(state, "registrations")
        time.sleep(6)
        assert (state / "registrations").read_text().splitlines() == ["registered"]
        (state / "offer").touch()
        marker(state, "interrupted")
        assert json.loads(database("event", f"{board}:7")) == ["fixture-lane"]
        assert not (state / "acked").exists()
        first.kill()
        first.wait(timeout=10)

        launch("legacy")
        marker(state, "acked")
        assert (state / "acked").read_text() == "redelivered"
        assert json.loads(database("event", f"{board}:7")) == ["fixture-lane"]
        time.sleep(6)
        assert (state / "registrations").read_text().splitlines() == ["registered", "registered"]
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
            "Bridge smoke passed: durable commit before ack, one inbound event on redelivery, "
            "and one leased outbound send."
        )
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


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
