"""The same Plotroom tools through both local transports and MCP protocol eras."""

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from conftest import BRIEF_TICKET_BODY, BRIEF_TICKET_REF, make_brief_ticket, write_config
from drain_standin import DrainStandIn
from fakes import FixedClock
from plotroom.adapters.github import GitHubPoller
from plotroom.adapters.work_items_bmad import BmadTicketTree
from plotroom.app.config import load_config
from plotroom.app.serve import Daemon
from plotroom.app.tokens import IssuedToken, TokenRegistry
from plotroom.core.events import NewEvent
from plotroom.core.github import ObservedPull, apply_pull_poll
from plotroom.core.github_projection import CHECKS, GitHubRecord, GitHubResource, apply_resource
from plotroom.core.snapshot import estimate_tokens
from plotroom.core.watchers import Watchers
from rawproxy import ERAS, MODERN, initialize_request, modern_call, spawn_proxy

LEGACY = "2025-06-18"
FIXTURE_REPO = "fixture/project"
FIXTURE_TIME = "2026-11-23T12:00:00.000000Z"
CHECK_TIME = "2026-11-23T11:59:59.000000Z"
TOOL_NAMES = {
    "next",
    "snapshot",
    "search",
    "brief",
    "merge_gate",
    "freeze",
    "unfreeze",
    "ack",
    "wait",
    "lane_start",
    "lane_update",
    "lane_ready",
    "lane_archive",
    "checklist_done",
    "checklist_import",
    "clerk_import",
    "deploy_report",
    "run_report",
    "budget_open",
    "budget_status",
    "route",
    "route_request",
    "claim",
    "release",
    "claims",
    "overlaps",
    "ingest",
    "trigger",
    "triggers",
    "cancel",
    "outbox_lease",
    "outbox_done",
}
WRITER_TOOLS = {
    "ack",
    "freeze",
    "unfreeze",
    "claim",
    "release",
    "lane_start",
    "lane_update",
    "lane_ready",
    "lane_archive",
    "checklist_done",
    "checklist_import",
    "deploy_report",
    "run_report",
    "budget_open",
    "route",
    "route_request",
    "trigger",
    "cancel",
    "outbox_lease",
    "outbox_done",
}
REPORT_ARGUMENTS = {
    "deploy_report": {
        "lane": "lane",
        "environment": "fixture",
        "sha": "a" * 40,
        "state": "success",
    },
    "run_report": {"lane": "lane", "run_id": "fixture-run", "state": "succeeded"},
}
BUDGET_ARGUMENTS: dict[str, Any] = {
    "run": "fixture-paid-run",
    "lane": "lane",
    "cap_usd": 1.25,
    "provider": "fixture",
    "key_scope": "staging",
}
ROUTE_ARGUMENTS = {
    "route": {"action_kind": "publish", "lane": "lane"},
    "route_request": {"action_kind": "publish"},
}
type Message = dict[str, Any]
type Request = Callable[[Message], Awaitable[Message]]


@dataclass(frozen=True)
class Endpoint:
    url: str
    config: Path
    token: IssuedToken
    drain: IssuedToken
    worker: IssuedToken
    departed: asyncio.Queue[str]
    body_reads: list[str] = field(default_factory=lambda: [])


@dataclass(frozen=True, kw_only=True)
class SearchEndpoint(Endpoint):
    other: IssuedToken
    search_hit: str
    other_search_hit: str


@pytest.fixture
async def endpoint(
    tmp_path: Path, token_home: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[SearchEndpoint]:
    config = write_config(tmp_path)
    tree, installation = make_brief_ticket(tmp_path)
    config.write_text(
        "search_budget = 256\n"
        + config.read_text()
        + f'ticket_tree = "{tree}"\nbmad_installation = "{installation}"\n'
        'drain_session = "fixture-drain"\n[projects.demo.workers.fixture]\n'
        'claude_session = "fixture-session"\n[projects.demo.github]\n'
        'repository = "fixture/project"\nmin_checks = 1\n'
        "[projects.demo.workers.first]\n[projects.demo.workers.second]\n"
        "[projects.demo.brief]\n"
        'plan = "Inspect, change, verify."\nresult = "Status and evidence."\n'
        'standing_rules = ["Use fixture data.", "Keep {worker} literal."]\n'
        'template = """\n'
        "Brief {worker} for {work_item}\n\n{source_text}\n"
        "Paths\n{paths}\n\nDon't touch\n{dont_touch}\n\n"
        'Plan\n{plan}\n\nRESULT\n{result}\n\nRules\n{standing_rules}\n"""\n'
        "[projects.other]\n",
        encoding="utf-8",
    )
    options = load_config(config, {})
    registry = TokenRegistry(options.data_dir)
    token = registry.issue("demo", "orchestrator", local=True)
    drain_token = registry.issue("demo", "drain", local=True)
    worker_token = registry.issue("demo", "worker:fixture", local=True)
    other_token = registry.issue("other", "orchestrator", local=True)
    daemon = Daemon(options, clock=FixedClock(datetime.fromisoformat(FIXTURE_TIME)))
    departed: asyncio.Queue[str] = asyncio.Queue()
    body_reads: list[str] = []
    waiting = Watchers.waiting
    read_body = BmadTicketTree.body

    async def observed_body(self: BmadTicketTree, ref: str) -> str | None:
        body_reads.append(ref)
        return await read_body(self, ref)

    monkeypatch.setattr(BmadTicketTree, "body", observed_body)

    @contextmanager
    def observed(self: Watchers, role: str) -> Generator[None]:
        try:
            with waiting(self, role):
                yield
        finally:
            departed.put_nowait(role)

    monkeypatch.setattr(Watchers, "waiting", observed)

    async def pause_poller(
        _self: GitHubPoller,
        _after_pull: Callable[[httpx.AsyncClient], Awaitable[None]] | None = None,
    ) -> None:
        await asyncio.Future[None]()

    monkeypatch.setattr(GitHubPoller, "run", pause_poller)
    serving = asyncio.create_task(daemon.run())
    try:
        async with asyncio.timeout(10):
            port = await daemon.wait_started()
        projections = (
            ObservedPull(1, "demo", "a" * 40, FIXTURE_REPO, "open", FIXTURE_TIME, ""),
            ObservedPull(2, "merged", "b" * 40, FIXTURE_REPO, "merged", FIXTURE_TIME, ""),
        )
        ledger = daemon.ledgers["demo"]
        await ledger.run(
            "system",
            lambda uow: apply_pull_poll(uow, FIXTURE_REPO, None, projections, FIXTURE_TIME),
        )
        head = projections[0].head_sha
        check = GitHubRecord(
            "1",
            "check_run",
            {"id": 1, "name": "fixture", "status": "in_progress", "head_sha": head},
            details={"pull_numbers": [1]},
        )
        await ledger.run(
            "system",
            lambda uow: apply_resource(
                uow, FIXTURE_REPO, CHECKS, head, GitHubResource((check,)), CHECK_TIME
            ),
        )
        ledger.github_error = None
        search_hit = await ledger.append(
            "system",
            NewEvent(
                "switchboard", "switchboard.message", "fixture", {"sender": "test"}, "needle demo"
            ),
        )
        other_search_hit = await daemon.ledgers["other"].append(
            "system",
            NewEvent(
                "switchboard", "switchboard.message", "fixture", {"sender": "test"}, "needle other"
            ),
        )
        for index in range(4):
            await ledger.append(
                "system",
                NewEvent(
                    "switchboard",
                    "switchboard.message",
                    f"budget-{index}",
                    {"sender": "test"},
                    "budgetword " + "界" * 260,
                ),
            )
        yield SearchEndpoint(
            f"http://127.0.0.1:{port}/mcp",
            config,
            token,
            drain_token,
            worker_token,
            departed,
            body_reads,
            other=other_token,
            search_hit=search_hit.event.id,
            other_search_hit=other_search_hit.event.id,
        )
    finally:
        daemon.stop()
        async with asyncio.timeout(10):
            await serving


def message(era: str, number: int, method: str, **params: Any) -> Message:
    if era == "modern":
        request = modern_call(number, "snapshot", {})
        request["method"] = method
        params["_meta"] = {**request["params"]["_meta"], **params.get("_meta", {})}
        request["params"] = params
        return request
    return {"jsonrpc": "2.0", "id": number, "method": method, "params": params}


def headers(era: str, request: Message) -> dict[str, str]:
    result = {"accept": "application/json, text/event-stream"}
    method = request["method"]
    if era == "modern":
        result.update({"mcp-protocol-version": MODERN, "mcp-method": method})
        if method == "tools/call":
            result["mcp-name"] = request["params"]["name"]
    elif method != "initialize":
        result["mcp-protocol-version"] = LEGACY
    return result


async def messages(response: httpx.Response) -> AsyncGenerator[Message]:
    assert response.status_code == 200
    if "text/event-stream" not in response.headers["content-type"]:
        await response.aread()
        yield response.json()
        return
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            yield json.loads(line[6:])


def initialize() -> Message:
    request = initialize_request()
    request["params"]["protocolVersion"] = LEGACY
    return request


@asynccontextmanager
async def connection(endpoint: Endpoint, transport: str, era: str) -> AsyncGenerator[Request]:
    if transport == "http":
        async with httpx.AsyncClient(
            headers={"authorization": f"Bearer {endpoint.token.bearer}"}, trust_env=False
        ) as http:

            async def roundtrip(request: Message) -> Message:
                async with http.stream(
                    "POST", endpoint.url, json=request, headers=headers(era, request)
                ) as response:
                    async for reply in messages(response):
                        if reply.get("id") == request["id"]:
                            return reply
                pytest.fail(f"request {request['id']} received no answer")

            if era == "legacy":
                assert (await roundtrip(initialize()))["result"]["protocolVersion"] == LEGACY
                await http.post(
                    endpoint.url,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers={
                        "accept": "application/json, text/event-stream",
                        "mcp-protocol-version": LEGACY,
                    },
                )
            yield roundtrip
    else:
        proxy = await spawn_proxy(
            "--config", str(endpoint.config), "--token-id", endpoint.token.token_id
        )
        try:

            async def roundtrip(request: Message) -> Message:
                proxy.send(request)
                return await proxy.answer(request["id"])

            if era == "legacy":
                assert (await roundtrip(initialize()))["result"]["protocolVersion"] == LEGACY
                proxy.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            yield roundtrip
        finally:
            await proxy.close()


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("era", ERAS)
async def test_every_registered_tool(endpoint: SearchEndpoint, transport: str, era: str) -> None:
    async with asyncio.timeout(15), connection(endpoint, transport, era) as send:
        if era == "modern":
            discovered = await send(message(era, 20, "server/discover"))
            assert "result" in discovered, discovered
        listed = await send(message(era, 1, "tools/list"))
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert names == TOOL_NAMES
        for tool in listed["result"]["tools"]:
            if tool["name"] in WRITER_TOOLS:
                assert "request_id" in tool["inputSchema"]["required"]
            if tool["name"] == "brief":
                assert set(tool["inputSchema"]["required"]) == {"work_item", "worker"}
                assert tool["inputSchema"]["properties"]["work_item"]["type"] == "string"
                assert tool["inputSchema"]["properties"]["worker"]["type"] == "string"
            if tool["name"] == "search":
                assert tool["inputSchema"]["required"] == ["text"]
                assert tool["inputSchema"]["properties"]["text"]["type"] == "string"
                assert tool["inputSchema"]["properties"]["limit"]["type"] == "integer"
            if tool["name"] in REPORT_ARGUMENTS:
                assert set(tool["inputSchema"]["required"]) == {
                    "request_id",
                    *REPORT_ARGUMENTS[tool["name"]],
                }
            if tool["name"] == "budget_open":
                schema = tool["inputSchema"]
                assert set(schema["required"]) == {"request_id", *BUDGET_ARGUMENTS}
                assert schema["properties"]["cap_usd"]["type"] == "number"
            if tool["name"] == "budget_status":
                assert set(tool["inputSchema"]["properties"]) == {"run"}
            if route_args := ROUTE_ARGUMENTS.get(tool["name"]):
                schema = tool["inputSchema"]
                assert set(schema["required"]) == {"request_id", *route_args}
                assert all(schema["properties"][field]["type"] == "string" for field in route_args)
        called: set[str] = set()
        request_ids = count(21)

        async def call(name: str, *, error: str | None = None, **args: Any) -> dict[str, Any]:
            called.add(name)
            number = next(request_ids)
            if name in WRITER_TOOLS:
                args["request_id"] = f"{name}-{number}"
            reply = await send(message(era, number, "tools/call", name=name, arguments=args))
            assert "result" in reply, reply
            assert not reply["result"].get("isError", False), reply
            content = reply["result"]["content"][0]["text"]
            if name == "search":
                assert estimate_tokens(content) <= 256
            payload = json.loads(content)
            if error is not None:
                assert payload["error"]["code"] == error, payload
                return {}
            assert payload["ok"], payload
            return payload["data"]

        assert (await call("snapshot"))["lanes"] == {}
        assert await call("search", text="needle") == {
            "hits": [
                {"id": endpoint.search_hit, "kind": "switchboard.message", "excerpt": "needle demo"}
            ],
            "omitted": 0,
        }
        assert await call("search", text="needle", limit=0, error="invalid") == {}
        capped = await call("search", text="budgetword", limit=2)
        assert len(capped["hits"]) == 1
        assert capped["omitted"] == 3
        limited = await call("search", text="budgetword", limit=1)
        assert limited == capped
        query = message(era, 80, "tools/call", name="search", arguments={"text": "needle"})
        async with connection(replace(endpoint, token=endpoint.other), transport, era) as other:
            reply = await other(query)
            assert json.loads(reply["result"]["content"][0]["text"])["data"] == {
                "hits": [
                    {
                        "id": endpoint.other_search_hit,
                        "kind": "switchboard.message",
                        "excerpt": "needle other",
                    }
                ],
                "omitted": 0,
            }
        assert (
            BRIEF_TICKET_BODY
            in (await call("brief", work_item=BRIEF_TICKET_REF, worker="fixture"))["brief"]
        )
        gate = await call("merge_gate", pr=1)
        assert gate["verdict"] == "REFUSE"
        assert (gate["head_sha"], gate["as_of"]) == ("a" * 40, CHECK_TIME)
        assert (gate["reasons"][0]["code"], gate["exit_class"]) == ("checks_pending", 2)
        assert await call("freeze", reason="local deploy window") == {
            "frozen": True,
            "reason": "local deploy window",
        }
        frozen = await call("merge_gate", pr=1)
        assert frozen["verdict"] == "REFUSE"
        assert (frozen["head_sha"], frozen["as_of"]) == (gate["head_sha"], gate["as_of"])
        assert frozen["reasons"] == [
            {"code": "deploy_window", "detail": "local deploy window", "exit_class": 2}
        ]
        assert (await call("snapshot"))["freezes"] == {"deploy": {"reason": "local deploy window"}}
        assert await call("unfreeze") == {"frozen": False, "reason": None}
        assert await call("merge_gate", pr=1) == gate
        assert "freezes" not in await call("snapshot")
        await call("lane_start", lane="lane", worker="fixture", branch="demo", work_items=[])
        assignment = ROUTE_ARGUMENTS["route"]
        assert await call("route", **assignment) == assignment
        assert await call("route_request", **ROUTE_ARGUMENTS["route_request"]) == assignment
        assert await call("budget_status") == {"budgets": []}
        for overrides in (
            {"cap_usd": 0},
            {"cap_usd": True},
            {"cap_usd": "1"},
            {"key_scope": "fixture key material"},
        ):
            number = next(request_ids)
            reply = await send(
                message(
                    era,
                    number,
                    "tools/call",
                    name="budget_open",
                    arguments={
                        **BUDGET_ARGUMENTS,
                        **overrides,
                        "request_id": f"invalid-budget-{number}",
                    },
                )
            )
            result = reply["result"]
            if not result.get("isError", False):
                payload = json.loads(result["content"][0]["text"])
                assert payload["error"]["code"] == "invalid"
        assert await call("budget_status") == {"budgets": []}
        opened = await call("budget_open", **BUDGET_ARGUMENTS)
        assert {key: opened[key] for key in BUDGET_ARGUMENTS} == BUDGET_ARGUMENTS
        assert await call("budget_status", run="fixture-paid-run") == {"budgets": [opened]}
        for name, args in REPORT_ARGUMENTS.items():
            reply = await send(
                message(
                    era,
                    next(request_ids),
                    "tools/call",
                    name=name,
                    arguments={**args, "request_id": f"orchestrator-{name}"},
                )
            )
            payload = json.loads(reply["result"]["content"][0]["text"])
            assert payload["error"]["code"] == "forbidden"
        async with connection(replace(endpoint, token=endpoint.worker), transport, era) as worker:
            for name, args in REPORT_ARGUMENTS.items():
                reply = await worker(
                    message(
                        era,
                        next(request_ids),
                        "tools/call",
                        name=name,
                        arguments={**args, "request_id": f"worker-{name}"},
                    )
                )
                payload = json.loads(reply["result"]["content"][0]["text"])
                assert payload["ok"], payload
                called.add(name)
        await call("lane_update", lane="lane", waiting_on="orchestrator")
        item = (await call("next"))["items"][0]
        assert (await call("wait", cursor=0, timeout=0))["item"] == item
        assert (await call("ack", item=item["id"]))["state"] == "snoozed"
        missing = await call("route_request", action_kind="inspect")
        assert missing["lane"] is None
        assert await call("route_request", action_kind="inspect") == missing
        [route_item] = [
            row for row in (await call("next"))["items"] if row["id"] == missing["item"]
        ]
        assert route_item["dedup_key"] == "route_missing:inspect"
        await call("route", action_kind="inspect", lane="lane")
        assert (await call("lane_ready", lane="lane", pr=1, head_sha="a" * 40))["state"] == "ready"
        await call("checklist_done", lane="lane", step="fixture", error="conflict")
        local_only = "forbidden" if transport == "http" else None
        rows = [{"step": "Verify artifact", "owner": "fixture", "ref": "fixture:ledger"}]
        checklist = await call("checklist_import", lane="lane", rows=rows, error=local_only)
        imported = await call("clerk_import", triggers=[], pulls=[], skips=[], error=local_only)
        if transport == "stdio":
            assert checklist["steps"] == [{**rows[0], "completion_ref": None}]
            assert imported == {"triggers": {}, "lanes": {}, "items": {}}
        assert (await call("claim", lane="lane", globs=["src/**"], reason="fixture"))["changed"]
        assert (await call("claims", lane="lane"))["claims"][0]["reason"] == "fixture"
        assert (await call("overlaps", lane="lane"))["items"] == []
        assert (await call("release", lane="lane", globs=["src/**"]))["changed"]
        assert (
            await call(
                "ingest",
                kind="claude.session_message",
                session_id="session",
                upstream_id="message",
                body="fixture",
                lane="lane",
            )
        )["created"]
        assert (await call("lane_archive", lane="lane"))["state"] == "archived"
        ratio = {"budget_ratio": {"run": "fixture-paid-run", "gte": 0.9}}
        trigger = await call(
            "trigger",
            predicate=ratio,
            action={"notify_orchestrator": {"text": "fixture"}},
        )
        [registered] = (await call("triggers"))["triggers"]
        assert (registered["id"], registered["predicate"], registered["state"]) == (
            trigger["id"],
            ratio,
            "open",
        )
        assert (await call("cancel", trigger_id=trigger["id"]))["state"] == "canceled"
        assert (await call("snapshot"))["triggers"]["canceled"] == 1
        fired = await call(
            "trigger",
            predicate={"pr_merged": {"number": 2}},
            action={"send": {"worker": "fixture", "text": "synthetic native delivery"}},
        )
        [firing] = [row for row in (await call("triggers"))["triggers"] if row["id"] == fired["id"]]
        assert firing["state"] == "fired"
        prior = await call(
            "trigger",
            predicate={"pr_merged": {"number": 2}},
            action={"notify_orchestrator": {"text": "historical fixture"}},
            imported_fired=True,
            error=local_only,
        )
        if transport == "stdio":
            [historical] = [
                row for row in (await call("triggers"))["triggers"] if row["id"] == prior["id"]
            ]
            assert historical["state"] == "fired"
            assert historical["action_key"] is None
            await call(
                "trigger",
                predicate={"pr_merged": {"number": 2}},
                action={"notify_orchestrator": {"text": "historical fixture"}},
                imported_fired=True,
                mode="every",
                error="invalid",
            )
        assert called | {"outbox_lease", "outbox_done"} == names

    async with (
        asyncio.timeout(15),
        connection(replace(endpoint, token=endpoint.drain), transport, era) as send,
    ):

        async def drain_call(name: str, **args: Any) -> dict[str, Any]:
            number = next(request_ids)
            if name in WRITER_TOOLS:
                args["request_id"] = f"drain-success-{number}"
            reply = await send(message(era, number, "tools/call", name=name, arguments=args))
            payload = json.loads(reply["result"]["content"][0]["text"])
            assert payload["ok"], payload
            result: dict[str, Any] = payload["data"]
            return result

        stand_in = DrainStandIn()
        lease = await stand_in.drain_one(drain_call)
        assert lease["action_key"] == firing["action_key"]
        assert stand_in.deliveries == {
            firing["action_key"]: ("fixture-session", "synthetic native delivery")
        }
        arguments: dict[str, dict[str, Any]] = {
            **REPORT_ARGUMENTS,
            **ROUTE_ARGUMENTS,
            "budget_open": BUDGET_ARGUMENTS.copy(),
            "ack": {"item": "fixture"},
            "brief": {"work_item": BRIEF_TICKET_REF, "worker": "fixture"},
            "search": {"text": "needle"},
            "merge_gate": {"pr": 1},
            "freeze": {"reason": "fixture"},
            "claim": {"lane": "fixture", "globs": ["src/**"], "reason": "fixture"},
            "release": {"lane": "fixture", "globs": ["src/**"]},
            "overlaps": {"lane": "fixture"},
            "lane_start": {
                "lane": "fixture",
                "worker": "fixture",
                "branch": "fixture",
                "work_items": [],
            },
            "lane_update": {"lane": "fixture", "waiting_on": None},
            "lane_ready": {"lane": "fixture", "pr": 1, "head_sha": "a" * 40},
            "lane_archive": {"lane": "fixture"},
            "checklist_done": {"lane": "fixture", "step": "fixture"},
            "checklist_import": {"lane": "fixture", "rows": []},
            "clerk_import": {"triggers": [], "pulls": [], "skips": []},
            "ingest": {"kind": "claude.session_message", "session_id": "fixture", "body": "test"},
            "trigger": {
                "predicate": {"pr_merged": {"number": 1}},
                "action": {"notify_orchestrator": {"text": "fixture"}},
            },
            "cancel": {"trigger_id": "fixture"},
            "wait": {"timeout": 0},
            "outbox_lease": {"consumer_id": stand_in.consumer_id},
            "outbox_done": {"act_id": "fixture"},
        }
        for number, name in enumerate(sorted(names), start=1):
            args = arguments.get(name, {}).copy()
            if name in WRITER_TOOLS:
                args["request_id"] = f"drain-{name}"
            reply = await send(message(era, number, "tools/call", name=name, arguments=args))
            assert not reply["result"].get("isError", False), reply
            payload = json.loads(reply["result"]["content"][0]["text"])
            if name == "wait":
                assert payload["ok"]
                assert payload["data"]["item"] is None
            elif name == "outbox_lease":
                assert payload["ok"]
                assert payload["data"]["lease"] is None
            else:
                code = "conflict" if name == "outbox_done" else "forbidden"
                assert payload["error"]["code"] == code

    async with (
        asyncio.timeout(15),
        connection(replace(endpoint, token=endpoint.worker), transport, era) as send,
    ):
        for number, name in enumerate(("outbox_lease", "outbox_done"), start=1):
            args = {"request_id": name}
            if name == "outbox_done":
                args["act_id"] = "fixture"
            else:
                args["consumer_id"] = str(uuid4())
            reply = await send(message(era, number, "tools/call", name=name, arguments=args))
            payload = json.loads(reply["result"]["content"][0]["text"])
            assert payload["error"]["code"] == "forbidden"
        for number, (name, args) in enumerate(
            (
                ("freeze", {"request_id": "freeze", "reason": "fixture"}),
                ("unfreeze", {"request_id": "unfreeze"}),
                ("budget_open", {**BUDGET_ARGUMENTS, "request_id": "worker-budget"}),
                ("budget_status", {"run": "fixture-paid-run"}),
                ("route", {**ROUTE_ARGUMENTS["route"], "request_id": "worker-route"}),
                (
                    "route_request",
                    {**ROUTE_ARGUMENTS["route_request"], "request_id": "worker-route-request"},
                ),
            ),
            start=3,
        ):
            reply = await send(message(era, number, "tools/call", name=name, arguments=args))
            payload = json.loads(reply["result"]["content"][0]["text"])
            assert payload["error"]["code"] == "forbidden"


@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_brief_reports_current_claims_exactly(endpoint: Endpoint, transport: str) -> None:
    numbering = count(1)

    async def call(send: Request, tool: str, **args: Any) -> dict[str, Any]:
        number = next(numbering)
        if tool in WRITER_TOOLS:
            args["request_id"] = f"brief-fixture-{number}"
        reply = await send(message("modern", number, "tools/call", name=tool, arguments=args))
        assert not reply["result"].get("isError", False), reply
        payload: dict[str, Any] = json.loads(reply["result"]["content"][0]["text"])
        return payload

    def expected(paths: str, dont_touch: str) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "brief": (
                    f"Brief fixture for 1\n\n{BRIEF_TICKET_BODY}\n"
                    f"Paths\n{paths}\n\nDon't touch\n{dont_touch}\n\n"
                    "Plan\nInspect, change, verify.\n\nRESULT\nStatus and evidence.\n\n"
                    "Rules\n- Use fixture data.\n- Keep {worker} literal.\n"
                )
            },
        }

    async with asyncio.timeout(15), connection(endpoint, transport, "modern") as send:

        async def start(lane: str, worker: str, path: str, reason: str) -> None:
            assert (
                await call(send, "lane_start", lane=lane, worker=worker, branch=lane, work_items=[])
            )["ok"]
            assert (await call(send, "claim", lane=lane, globs=[path], reason=reason))["ok"]

        await start("second-lane", "second", "docs/**", "copy")
        await start("first-lane", "first", "src/**", "implementation")
        other_claims = "- src/** (first-lane: implementation)\n- docs/** (second-lane: copy)"
        args = {"work_item": BRIEF_TICKET_REF, "worker": "fixture"}
        assert await call(send, "brief", **args) == expected("", other_claims)

        await start("brief-lane", "fixture", "tests/**", "verification")
        assert await call(send, "brief", **args) == expected(
            "- tests/** (verification)", other_claims
        )
        assert (await call(send, "release", lane="first-lane", globs=["src/**"]))["ok"]
        assert await call(send, "brief", **args) == expected(
            "- tests/** (verification)", "- docs/** (second-lane: copy)"
        )
        assert (await call(send, "brief", work_item=BRIEF_TICKET_REF, worker="absent"))["error"][
            "code"
        ] == "not_found"
        assert (await call(send, "brief", work_item="missing", worker="fixture"))["error"][
            "code"
        ] == "not_found"

    async with (
        asyncio.timeout(15),
        connection(replace(endpoint, token=endpoint.worker), transport, "modern") as send,
    ):
        assert (await call(send, "brief", **args))["error"]["code"] == "forbidden"
    assert endpoint.body_reads == [BRIEF_TICKET_REF, BRIEF_TICKET_REF, BRIEF_TICKET_REF, "missing"]


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_wait_progress_and_cancellation(endpoint: Endpoint, era: str, transport: str) -> None:
    wait = message(
        era,
        1,
        "tools/call",
        name="wait",
        arguments={"timeout": 90},
        _meta={"progressToken": "watch"},
    )
    snapshot = message(era, 2, "tools/call", name="snapshot", arguments={})
    async with asyncio.timeout(10):
        if transport == "http":
            async with httpx.AsyncClient(
                headers={"authorization": f"Bearer {endpoint.token.bearer}"}, trust_env=False
            ) as http:
                async with http.stream(
                    "POST", endpoint.url, json=wait, headers=headers(era, wait)
                ) as response:
                    async for reply in messages(response):
                        if reply.get("method") == "notifications/progress":
                            assert reply["params"]["progressToken"] == "watch"
                            break
                    else:
                        pytest.fail("wait stream closed before progress")
                # A stateless HTTP client cancels a request by closing its response stream.
                assert await endpoint.departed.get() == "orchestrator"
                async with http.stream(
                    "POST", endpoint.url, json=snapshot, headers=headers(era, snapshot)
                ) as response:
                    assert any([m.get("id") == 2 async for m in messages(response)])
        else:
            proxy = await spawn_proxy(
                "--config", str(endpoint.config), "--token-id", endpoint.token.token_id
            )
            try:
                if era == "legacy":
                    proxy.send(initialize())
                    assert (await proxy.answer(0))["result"]["protocolVersion"] == LEGACY
                    proxy.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                proxy.send(wait)
                while not any(
                    reply.get("method") == "notifications/progress" for reply in proxy.messages
                ):
                    proxy.changed.clear()
                    await proxy.changed.wait()
                proxy.send(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/cancelled",
                        "params": {"requestId": 1},
                    }
                )
                assert await endpoint.departed.get() == "orchestrator"
                proxy.send(snapshot)
                assert "result" in await proxy.answer(2)
                assert not any(reply.get("id") == 1 for reply in proxy.messages)
            finally:
                await proxy.close()


@pytest.mark.parametrize("transport", ["http", "stdio"])
async def test_modern_listen_stays_open_while_tools_answer(
    endpoint: Endpoint, transport: str
) -> None:
    listen = message("modern", 1, "subscriptions/listen", notifications={"toolsListChanged": True})
    listing = message("modern", 2, "tools/list")
    async with asyncio.timeout(10):
        if transport == "http":
            async with (
                httpx.AsyncClient(
                    headers={"authorization": f"Bearer {endpoint.token.bearer}"}, trust_env=False
                ) as http,
                http.stream(
                    "POST", endpoint.url, json=listen, headers=headers("modern", listen)
                ) as response,
            ):
                received = messages(response)
                first = await anext(received)
                assert "subscriptionId" in str(first)
                pending = asyncio.create_task(anext(received))
                try:
                    async with http.stream(
                        "POST", endpoint.url, json=listing, headers=headers("modern", listing)
                    ) as listed:
                        assert {
                            tool["name"]
                            for tool in (await anext(messages(listed)))["result"]["tools"]
                        } == TOOL_NAMES
                    assert not pending.done(), (
                        "subscription ended while the client was still listening"
                    )
                finally:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
        else:
            proxy = await spawn_proxy(
                "--config", str(endpoint.config), "--token-id", endpoint.token.token_id
            )
            try:
                proxy.send(listen)
                while not any("subscriptionId" in str(reply) for reply in proxy.messages):
                    proxy.changed.clear()
                    await proxy.changed.wait()
                proxy.send(listing)
                assert {
                    tool["name"] for tool in (await proxy.answer(2))["result"]["tools"]
                } == TOOL_NAMES
                assert proxy.process.returncode is None
            finally:
                await proxy.close()
            assert proxy.process.returncode == 0


async def test_snapshot_and_next_text_respect_the_budget(endpoint: Endpoint) -> None:
    branch = "x" * 7550
    async with asyncio.timeout(10), connection(endpoint, "http", "legacy") as send:

        async def call(number: int, name: str, **args: Any) -> str:
            result = await send(message("legacy", number, "tools/call", name=name, arguments=args))
            return str(result["result"]["content"][0]["text"])

        assert json.loads(
            await call(
                1,
                "lane_start",
                request_id="start",
                lane="lane",
                worker="fixture",
                branch=branch,
                work_items=[],
            )
        )["ok"]
        assert json.loads(
            await call(2, "lane_update", request_id="wait", lane="lane", waiting_on="orchestrator")
        )["ok"]
        snapshot = await call(3, "snapshot")
        assert estimate_tokens(snapshot) <= 2000
        board = json.loads(snapshot)["data"]
        assert board["left_out"] == 1
        assert board["lane_details"] == [{"id": "lane", "details_omitted": True}]
        page = await call(4, "next")
        assert estimate_tokens(page) <= 2000
        item = json.loads(page)["data"]["items"][0]
        assert item["details_omitted"] is True
        assert (
            json.loads(await call(5, "next", item=item["id"]))["data"]["context"]["branch"]
            == branch
        )
        assert json.loads(await call(6, "snapshot", lane="lane"))["data"]["branch"] == branch
