"""Epic 3 acceptance through a local daemon, using only deterministic inputs."""

import asyncio
import json
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from itertools import count
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest

from conftest import write_config
from drain_standin import DrainStandIn
from fakes import FixedClock
from plotroom.adapters.github import GitHubPoller, LocalGitHubCredential, PullRequestFetcher
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.app.config import load_config
from plotroom.app.serve import Daemon
from plotroom.app.tokens import IssuedToken, TokenRegistry
from plotroom.core.ledger import Ledger
from plotroom.core.triggers import TRIGGER_FIRED
from test_transport_contract import Endpoint, connection, message

REPO = "fixture/project"
MAIN_SHA = "a" * 40
PREVIOUS_SHA = "b" * 40
AS_OF = "2026-11-22T12:00:00.000000Z"
API = "https://api.github.com"
CREDENTIAL = LocalGitHubCredential("FIXTURE", {"FIXTURE": "fixture-token"}, None)
pytestmark = pytest.mark.parametrize("transport", ["http", "stdio"])
type ToolCall = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class LocalPlotroom:
    config: Path
    orchestrator: IssuedToken
    drain: IssuedToken
    worker: IssuedToken
    other_tokens: tuple[IssuedToken, ...]

    @asynccontextmanager
    async def serve(self) -> AsyncGenerator[tuple[Endpoint, Ledger]]:
        daemon = Daemon(load_config(self.config, {}), clock=FixedClock())
        serving = asyncio.create_task(daemon.run())
        try:
            async with asyncio.timeout(10):
                port = await daemon.wait_started()
            yield (
                Endpoint(
                    f"http://127.0.0.1:{port}/mcp",
                    self.config,
                    self.orchestrator,
                    self.drain,
                    self.worker,
                    asyncio.Queue(),
                ),
                daemon.ledgers["demo"],
            )
        finally:
            daemon.stop()
            async with asyncio.timeout(10):
                await serving


@pytest.fixture
def local_plotroom(
    tmp_path: Path, token_home: Path, monkeypatch: pytest.MonkeyPatch
) -> LocalPlotroom:
    async def pause_github(_self: GitHubPoller, _details: Any = None) -> None:
        await asyncio.Future[None]()

    # Drive the GitHub adapters one poll at a time with an in-process HTTP fixture.
    monkeypatch.setattr(GitHubPoller, "run", pause_github)
    child = Path(__file__).with_name("switchboard_child.py")
    command = json.dumps([sys.executable, str(child), str(tmp_path)])
    config = write_config(tmp_path)
    with config.open("a", encoding="utf-8") as output:
        output.write(
            'drain_session = "fixture-drain"\n'
            '[projects.demo.workers.relay]\nswitchboard_address = "fixture-peer"\n'
            '[projects.demo.workers.desktop]\nclaude_session = "fixture-session"\n'
            '[projects.demo.switchboard]\nboard = "fixture-board"\n'
            f"command = {command}\n"
            f'[projects.demo.github]\nrepository = "{REPO}"\nimage_jobs = ["build-image"]\n'
        )
    for ready in ("available", "acked", "send-first"):
        (tmp_path / ready).touch()
    registry = TokenRegistry(load_config(config, {}).data_dir)
    return LocalPlotroom(
        config,
        registry.issue("demo", "orchestrator", local=True),
        registry.issue("demo", "drain", local=True),
        registry.issue("demo", "worker:relay", local=True),
        tuple(registry.issue("demo", role, local=True) for role in ("clerk", "bridge")),
    )


@asynccontextmanager
async def tools(
    endpoint: Endpoint, transport: str, *, token: IssuedToken | None = None
) -> AsyncGenerator[ToolCall]:
    async with connection(
        replace(endpoint, token=token or endpoint.token), transport, "modern"
    ) as send:
        request_ids = count(1)

        async def call(name: str, *, error: str | None = None, **arguments: Any) -> dict[str, Any]:
            reply = await send(
                message("modern", next(request_ids), "tools/call", name=name, arguments=arguments)
            )
            assert "result" in reply, reply
            assert not reply["result"].get("isError"), reply
            payload = json.loads(reply["result"]["content"][0]["text"])
            if error is not None:
                assert payload["error"]["code"] == error, payload
                problem: dict[str, Any] = payload["error"]
                return problem
            assert payload["ok"], payload
            data: dict[str, Any] = payload["data"]
            return data

        yield call


def drain_tools(call: ToolCall) -> ToolCall:
    request_ids = count(1)

    async def execute(name: str, **args: Any) -> dict[str, Any]:
        if name in {"outbox_lease", "outbox_done"}:
            args.setdefault("request_id", f"drain-{next(request_ids)}")
        return await call(name, **args)

    return execute


def git_fixture(routes: dict[str, Any]) -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer fixture-token"
        assert request.url.path.startswith(f"/repos/{REPO}")
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        return httpx.Response(200, json=routes[path])

    return httpx.MockTransport(respond)


async def observe_pull(ledger: Ledger, number: int, state: Literal["open", "merged"]) -> None:
    pull = {
        "number": number,
        "state": "closed" if state == "merged" else "open",
        "updated_at": AS_OF,
        "head": {"ref": f"branch-{number}", "sha": f"{number:040x}", "repo": {"full_name": REPO}},
        "merged_at": AS_OF if state == "merged" else None,
        "body": "",
    }
    poller = GitHubPoller(ledger, FixedClock(), REPO, CREDENTIAL, PullRequestFetcher(API, REPO), 1)
    async with httpx.AsyncClient(transport=git_fixture({"/pulls": [pull]})) as client:
        assert await poller.poll(client)


async def trigger_rows(call: ToolCall) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in (await call("triggers"))["triggers"]}


async def test_all_of_sends_once_after_restart(
    local_plotroom: LocalPlotroom, transport: str
) -> None:
    async with asyncio.timeout(15), local_plotroom.serve() as (endpoint, ledger):
        await observe_pull(ledger, 1, "open")
        await observe_pull(ledger, 2, "open")
        async with tools(endpoint, transport) as call:
            trigger = await call(
                "trigger",
                request_id="merge-pair",
                predicate={"all_of": [{"pr_merged": {"number": 1}}, {"pr_merged": {"number": 2}}]},
                action={"send": {"worker": "desktop", "text": "Both pull requests merged"}},
            )
            trigger_id = trigger["id"]
            await observe_pull(ledger, 1, "merged")
            before_restart = (await trigger_rows(call))[trigger_id]
            assert before_restart["state"] == "open"
            assert before_restart["action_key"] is None

    async with asyncio.timeout(15), local_plotroom.serve() as (endpoint, ledger):
        async with (
            tools(endpoint, transport) as call,
            tools(endpoint, transport, token=endpoint.drain) as drain,
        ):
            assert (await trigger_rows(call))[trigger_id] == before_restart
            assert (await drain("wait", timeout=0))["item"] is None
            await observe_pull(ledger, 2, "merged")
            fired = (await trigger_rows(call))[trigger_id]
            assert fired["state"] == "fired"
            receiver = DrainStandIn()
            drain_call = drain_tools(drain)
            receipt = await receiver.drain_one(drain_call)
            assert receipt["action_key"] == fired["action_key"]
            assert receiver.deliveries == {
                fired["action_key"]: ("fixture-session", "Both pull requests merged")
            }
            await observe_pull(ledger, 2, "merged")
            assert (await trigger_rows(call))[trigger_id] == fired
            assert (await drain_call("outbox_lease", consumer_id=receiver.consumer_id))[
                "lease"
            ] is None
        events = await ledger.events_after(0)
        [firing] = [event for event in events if event.kind == TRIGGER_FIRED]
        assert (firing.fields["trigger_id"], firing.seq) == (trigger_id, fired["fired_seq"])
        assert [
            event.fields["action_key"] for event in events if event.kind == "core.action_done"
        ] == [receipt["action_key"]]


async def test_sends_reach_relay_and_drain_with_keys(
    local_plotroom: LocalPlotroom, transport: str, tmp_path: Path
) -> None:
    async with asyncio.timeout(15), local_plotroom.serve() as (endpoint, ledger):
        async with (
            tools(endpoint, transport) as call,
            tools(endpoint, transport, token=endpoint.worker) as worker,
            tools(endpoint, transport, token=endpoint.drain) as drain,
        ):
            await call(
                "lane_start",
                request_id="start",
                lane="lane",
                worker="relay",
                branch="branch-1",
                work_items=[],
            )
            ids: dict[str, str] = {}
            for target in ("relay", "desktop"):
                trigger = await call(
                    "trigger",
                    request_id=target,
                    predicate={"deploy_finished": {"environment": "fixture", "sha": MAIN_SHA}},
                    action={"send": {"worker": target, "text": f"Fixture delivery to {target}"}},
                )
                ids[target] = trigger["id"]
            assert await worker(
                "deploy_report",
                request_id="reported",
                lane="lane",
                environment="fixture",
                sha=MAIN_SHA,
                state="success",
            )
            rows = await trigger_rows(call)
            assert all(rows[trigger_id]["state"] == "fired" for trigger_id in ids.values())
            relay_key, drain_key = (
                rows[ids[target]]["action_key"] for target in ("relay", "desktop")
            )
            assert relay_key
            assert drain_key
            assert relay_key != drain_key
            receiver = DrainStandIn()
            assert (await receiver.drain_one(drain_tools(drain)))["action_key"] == drain_key
            assert receiver.deliveries == {
                drain_key: ("fixture-session", "Fixture delivery to desktop")
            }
        # Wait for the daemon to commit the relay acknowledgement before inspecting the script receipt.
        while True:
            committed = ledger.commit_signal
            if any(
                event.kind == "core.action_done" and event.fields["action_key"] == relay_key
                for event in await ledger.events_after(0)
            ):
                break
            await committed.wait()
        send = json.loads((tmp_path / "send-retry").read_text())
        assert send["to"] == "fixture-peer"
        assert send["idempotency_key"] == relay_key
        assert json.loads(send["body"]) == {
            "action_key": relay_key,
            "body": "Fixture delivery to relay",
        }


async def test_only_orchestrator_creates_triggers_and_free_text_is_refused(
    local_plotroom: LocalPlotroom, transport: str
) -> None:
    predicate = {"pr_merged": {"number": 1}}
    action = {"notify_orchestrator": {"text": "Fixture trigger"}}
    async with asyncio.timeout(15), local_plotroom.serve() as (endpoint, _):
        for token in (endpoint.worker, endpoint.drain, *local_plotroom.other_tokens):
            async with tools(endpoint, transport, token=token) as call:
                await call(
                    "trigger",
                    request_id="forbidden",
                    predicate=predicate,
                    action=action,
                    error="forbidden",
                )
        async with tools(endpoint, transport) as call:
            await call(
                "trigger",
                request_id="invalid",
                predicate={"pr_merged": "after the pull request merges"},
                action=action,
                error="invalid",
            )
            assert await trigger_rows(call) == {}
            created = await call("trigger", request_id="valid", predicate=predicate, action=action)
            assert (await trigger_rows(call))[created["id"]]["state"] == "open"


def workflow(run_id: int, attempt: int, sha: str) -> dict[str, Any]:
    return {
        "id": run_id,
        "run_attempt": attempt,
        "name": "CI",
        "head_sha": sha,
        "status": "completed",
        "conclusion": "success",
        "created_at": AS_OF,
        "event": "push",
        "head_branch": "main",
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
    }


async def observe_main(
    ledger: Ledger,
    run: dict[str, Any],
    *,
    image_conclusion: str,
    previous: dict[str, Any] | None = None,
) -> None:
    runs = [run] if previous is None else [run, previous]
    routes: dict[str, Any] = {
        "": {"default_branch": "main"},
        "/branches/main": {"commit": {"sha": MAIN_SHA}},
        "/actions/runs": {"workflow_runs": runs},
        "/issues": [],
        "/issues/comments": [],
        "/deployments": [],
        f"/commits/{MAIN_SHA}/check-runs": {"total_count": 0, "check_runs": []},
        f"/commits/{PREVIOUS_SHA}/check-runs": {"total_count": 0, "check_runs": []},
    }
    for row in runs:
        run_id, attempt = row["id"], row["run_attempt"]
        routes[f"/actions/runs/{run_id}/attempts/{attempt}/jobs"] = {
            "jobs": [
                {
                    "id": run_id * 10 + attempt,
                    "name": "build-image",
                    "status": "completed",
                    "conclusion": image_conclusion if row is run else "success",
                }
            ],
        }
    poller = GitHubDetailPoller(
        ledger, FixedClock(), GitHubReadClient(API, REPO), CREDENTIAL, ("build-image",)
    )
    async with httpx.AsyncClient(transport=git_fixture(routes)) as client:
        await poller.poll(client)


async def test_worker_run_and_same_sha_main_image_readiness(
    local_plotroom: LocalPlotroom, transport: str
) -> None:
    async with (
        asyncio.timeout(15),
        local_plotroom.serve() as (endpoint, ledger),
        tools(endpoint, transport) as call,
        tools(endpoint, transport, token=endpoint.worker) as worker,
    ):
        await call(
            "lane_start",
            request_id="start",
            lane="lane",
            worker="relay",
            branch="branch-1",
            work_items=[],
        )
        run = await call(
            "trigger",
            request_id="run-trigger",
            lane="lane",
            predicate={"run_finished": {"run_id": "job"}},
            action={"notify_orchestrator": {"text": "Run finished"}},
        )
        main = await call(
            "trigger",
            request_id="main-trigger",
            predicate={"main_green_with_images": {"sha": MAIN_SHA}},
            action={"notify_orchestrator": {"text": "Main images ready"}},
        )
        await worker("run_report", request_id="running", lane="lane", run_id="job", state="running")
        previous = workflow(1, 1, PREVIOUS_SHA)
        await observe_main(ledger, previous, image_conclusion="success")
        rows = await trigger_rows(call)
        assert rows[run["id"]]["state"] == rows[main["id"]]["state"] == "open"
        await worker(
            "run_report", request_id="succeeded", lane="lane", run_id="job", state="succeeded"
        )
        failed_image = workflow(2, 1, MAIN_SHA)
        await observe_main(ledger, failed_image, image_conclusion="failure", previous=previous)
        rows = await trigger_rows(call)
        assert rows[run["id"]]["state"] == "fired"
        assert rows[main["id"]]["state"] == "open"
        passed_image = workflow(2, 2, MAIN_SHA)
        await observe_main(ledger, passed_image, image_conclusion="success", previous=previous)
        fired = await trigger_rows(call)
        assert fired[run["id"]] == rows[run["id"]]
        assert fired[main["id"]]["state"] == "fired"
        assert fired[main["id"]]["fired_seq"] > fired[run["id"]]["fired_seq"]
