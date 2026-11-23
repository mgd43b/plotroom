"""The five Epic 5 acceptance checks on a daemon with a fixture GitHub API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.client.client import Client

from conftest import authenticated_client, tool_json
from fakes import FixedClock
from plotroom.adapters.github import GitHubPoller
from plotroom.app.config import Config, GitHubConfig
from plotroom.app.serve import Daemon
from plotroom.app.tokens import TokenRegistry
from plotroom.core.claims import CLAIM_OVERLAP, PR_CLAIM_OVERLAP, TICKET_TREE_MOVE
from plotroom.core.github_projection import FILES

REPO = "fixture/repo"
API = "http://127.0.0.1:1"
TREE = "plan"
STORY = f"{TREE}/story.md"
WORKERS = frozenset({"alice", "bob", "carol"})


@dataclass
class Repository:
    pulls: dict[int, dict[str, Any]] = field(default_factory=lambda: dict[int, dict[str, Any]]())
    files: dict[int, list[dict[str, str]]] = field(
        default_factory=lambda: dict[int, list[dict[str, str]]]()
    )
    unavailable_files: set[int] = field(default_factory=lambda: set[int]())
    cycles: asyncio.Queue[asyncio.Future[None]] = field(
        default_factory=lambda: asyncio.Queue[asyncio.Future[None]]()
    )

    def pull(
        self,
        number: int,
        branch: str,
        *paths: str,
        previous: str | None = None,
        version: int = 1,
        state: str = "open",
        repository: str = REPO,
    ) -> None:
        assert previous is None or len(paths) == 1
        rows = [{"filename": path, "status": "modified"} for path in paths]
        if previous is not None:
            rows = [{"filename": paths[0], "status": "renamed", "previous_filename": previous}]
        self.files[number] = rows
        self.pulls[number] = {
            "number": number,
            "state": state,
            "merged_at": None,
            "body": "",
            "updated_at": f"2026-11-22T12:00:{version:02d}Z",
            "head": {
                "ref": branch,
                "sha": f"{number * 100 + version:040x}",
                "repo": {"full_name": repository},
            },
            "changed_files": len(rows),
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer fixture"
        if request.url.path == "/graphql":
            assert request.method == "POST"
            threads: dict[str, Any] = {
                "nodes": [],
                "pageInfo": {"endCursor": None, "hasNextPage": False},
            }
            return httpx.Response(
                200, json={"data": {"repository": {"pullRequest": {"reviewThreads": threads}}}}
            )
        assert request.method == "GET"
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        if path == "/pulls":
            return httpx.Response(200, json=list(self.pulls.values()))
        if path.startswith("/pulls/"):
            fields = path.split("/")
            number = int(fields[2])
            if len(fields) == 3:
                return httpx.Response(200, json=self.pulls[number])
            assert fields[3] == "files"
            if number in self.unavailable_files:
                return httpx.Response(503)
            return httpx.Response(200, json=self.files[number])
        if path.startswith("/commits/") and path.endswith("/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        if path == "":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/branches/main":
            return httpx.Response(200, json={"commit": {"sha": "a" * 40}})
        if path == "/actions/runs":
            return httpx.Response(200, json={"workflow_runs": []})
        assert path == "/issues" or path.startswith("/issues/"), path
        return httpx.Response(200, json=[])

    async def refresh(self) -> None:
        ready = asyncio.get_running_loop().create_future()
        self.cycles.put_nowait(ready)
        await asyncio.wait_for(ready, 10)


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> Repository:
    api = Repository()

    # Advance the daemon's real PR and detail pollers at deterministic checkpoints.
    async def drive(
        poller: GitHubPoller,
        after_pull: Callable[[httpx.AsyncClient], Awaitable[None]] | None = None,
    ) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(api.handle)) as client:
            while True:
                ready = await api.cycles.get()
                try:
                    assert await poller.poll(client)
                    assert after_pull is not None
                    await after_pull(client)
                except Exception as exc:
                    ready.set_exception(exc)
                else:
                    ready.set_result(None)

    monkeypatch.setattr(GitHubPoller, "run", drive)
    return api


@asynccontextmanager
async def running_daemon(
    directory: Path,
    *,
    configure_tree: bool = False,
    next_budget: int = 2000,
) -> AsyncGenerator[tuple[Daemon, Client, Client]]:
    secret = directory / "token"
    secret.write_text("fixture", encoding="utf-8")
    config = Config(
        directory / "data",
        "127.0.0.1",
        0,
        ("127.0.0.1:*",),
        ("demo",),
        workers={"demo": WORKERS},
        next_budget=next_budget,
        github={"demo": GitHubConfig(REPO, secret, API, 60)},
        github_ticket_trees={"demo": (TREE, Path.cwd())} if configure_tree else {},
    )
    tokens = TokenRegistry(config.data_dir)
    orch = tokens.issue("demo", "orchestrator", local=False)
    worker = tokens.issue("demo", "worker:alice", local=False)
    daemon = Daemon(config, clock=FixedClock())
    task = asyncio.create_task(daemon.run())
    try:
        port = await asyncio.wait_for(daemon.wait_started(), 10)
        url = f"http://127.0.0.1:{port}/mcp"
        async with (
            authenticated_client(url, orch.bearer) as orchestrator,
            authenticated_client(url, worker.bearer) as alice,
        ):
            yield daemon, orchestrator, alice
    finally:
        daemon.stop()
        await asyncio.wait_for(task, 10)


async def call(client: Client, name: str, **args: Any) -> dict[str, Any]:
    result = tool_json(await client.call_tool(name, args))
    assert result["ok"], result
    return result["data"]


async def start(client: Client, worker: str) -> None:
    await call(
        client,
        "lane_start",
        lane=worker,
        worker=worker,
        work_items=[],
        branch=worker,
        request_id=f"start-{worker}",
    )


async def claim(client: Client, lane: str, *paths: str, request_id: str = "claim") -> None:
    await call(
        client,
        "claim",
        lane=lane,
        globs=list(paths),
        reason=f"owned by {lane}",
        request_id=request_id,
    )


async def items(client: Client, kind: str) -> list[dict[str, Any]]:
    board = await call(client, "next")
    return [item for item in board["items"] if item["kind"] == kind]


async def test_pr_diff_raises_overlap_before_review(tmp_path: Path, repository: Repository) -> None:
    async with running_daemon(tmp_path) as (daemon, orch, alice):
        await start(orch, "alice")
        await start(orch, "bob")
        await claim(alice, "alice", "src/a/**")
        repository.pull(1, "alice", "src/a/own.py", "src/b/other.py")
        await repository.refresh()
        assert await items(orch, PR_CLAIM_OVERLAP) == []
        await claim(orch, "bob", "src/b/**")
        (first,) = await items(orch, PR_CLAIM_OVERLAP)
        assert first["context"] == {
            "lanes": ["alice", "bob"],
            "prs": [1],
            "claiming_lane": "bob",
            "matches": [
                {
                    "claim": {"lane": "bob", "glob": "src/b/**", "reason": "owned by bob"},
                    "paths": ["src/b/other.py"],
                }
            ],
        }
        assert (await call(orch, "snapshot", lane="alice"))["state"] == "active"
        repository.unavailable_files.add(1)
        await repository.refresh()
        cache = await daemon.ledgers["demo"].github_resource(REPO, FILES, "1")
        assert cache is not None
        assert not cache.complete
        assert await items(orch, PR_CLAIM_OVERLAP) == [first]
        repository.pull(1, "alice", "src/a/own.py", version=2)
        await repository.refresh()
        assert await items(orch, PR_CLAIM_OVERLAP) == []
        repository.unavailable_files.clear()
        repository.pull(2, "alice", "src/a/own.py", repository="fork/repo")
        await repository.refresh()
        (fork,) = await items(orch, PR_CLAIM_OVERLAP)
        assert fork["context"]["lanes"] == ["alice"]
        assert fork["context"]["prs"] == [2]
        repository.pull(2, "alice", "src/a/own.py", version=2, state="closed")
        await repository.refresh()
        assert await items(orch, PR_CLAIM_OVERLAP) == []


async def test_ticket_tree_move_lists_editing_prs_first(
    tmp_path: Path, repository: Repository
) -> None:
    async with running_daemon(tmp_path, configure_tree=True) as (_, orch, _):
        await start(orch, "alice")
        await start(orch, "bob")
        await start(orch, "carol")
        repository.pull(10, "alice", STORY)
        repository.pull(20, "bob", STORY)
        repository.pull(30, "carol", f"{TREE}/new.md", previous=STORY)
        repository.pull(40, "untouched", f"{TREE}/fresh.md", previous=f"{TREE}/untouched.md")
        repository.pull(50, "outside", "planning/new.md", previous="planning/story.md")
        repository.pull(60, "outside-editor", "planning/story.md")
        await repository.refresh()
        (move,) = await items(orch, TICKET_TREE_MOVE)
        assert move["context"] == {
            "lanes": ["alice", "bob", "carol"],
            "prs": [10, 20, 30],
            "moving_pr": 30,
            "editing_prs": [10, 20],
            "edits": {STORY: [10, 20]},
            "suggested_order": [10, 20, 30],
        }
        assert (await call(orch, "overlaps", pr=20))["items"] == [move]
        await repository.refresh()
        assert await items(orch, TICKET_TREE_MOVE) == [move]
        repository.pull(10, "alice", STORY, state="closed", version=2)
        repository.pull(20, "bob", "unrelated.md", version=2)
        await repository.refresh()
        assert await items(orch, TICKET_TREE_MOVE) == []
        repository.pull(20, "bob", f"{TREE}/alternative.md", previous=STORY, version=3)
        await repository.refresh()
        assert await items(orch, TICKET_TREE_MOVE) == []


async def test_conflicting_claim_reported_when_made(tmp_path: Path, repository: Repository) -> None:
    assert not repository.pulls
    async with running_daemon(tmp_path) as (_, orch, alice):
        await start(orch, "alice")
        await start(orch, "bob")
        await claim(alice, "alice", "src//a/**", "src/a/**")
        await claim(orch, "bob", "src/a/x.py", "src/a/y.py")
        (opened,) = await items(orch, CLAIM_OVERLAP)
        assert opened["context"]["lanes"] == ["alice", "bob"]
        assert [entry["glob"] for entry in opened["context"]["claims"]] == [
            "src/a/**",
            "src/a/x.py",
            "src/a/y.py",
        ]
        before = await call(orch, "snapshot")
        await claim(orch, "bob", "/src/a/x.py", "./src/a/y.py", request_id="retry")
        assert await call(orch, "snapshot") == before
        await call(
            orch, "release", lane="bob", globs=["src/a/x.py", "src/a/y.py"], request_id="release"
        )
        assert await items(orch, CLAIM_OVERLAP) == []
        await claim(alice, "alice", "config.yml", request_id="slashless")
        await claim(orch, "bob", "src/config.yml", request_id="nested")
        assert len(await items(orch, CLAIM_OVERLAP)) == 1
        await call(orch, "lane_archive", lane="bob", request_id="archive")
        assert await items(orch, CLAIM_OVERLAP) == []
        assert (await call(orch, "claims", lane="bob"))["claims"] == []


async def test_overlap_and_claim_reads_agree_with_board(
    tmp_path: Path, repository: Repository
) -> None:
    async with running_daemon(tmp_path) as (_, orch, alice):
        await start(orch, "alice")
        await start(orch, "bob")
        await claim(alice, "alice", "src/x")
        await claim(orch, "bob", *(f"private/path-{index:02}" for index in range(68)))
        repository.pull(3, "external", "src/x")
        await repository.refresh()
        (expected,) = await items(orch, PR_CLAIM_OVERLAP)
        assert (await call(orch, "overlaps", lane="alice"))["items"] == [expected]
        assert (await call(orch, "overlaps", pr=3))["items"] == [expected]
        assert (await call(orch, "overlaps", pr=3, offset=1))["items"] == []
        assert (await call(orch, "overlaps", pr=4))["items"] == []
        assert (await call(alice, "overlaps", lane="alice"))["items"] == []
        assert (await call(alice, "claims"))["claims"] == [
            {"lane": "alice", "glob": "src/x", "reason": "owned by alice"}
        ]
        assert (await call(alice, "claims", lane="bob"))["claims"] == []
        board = await call(orch, "snapshot")
        assert board["claims"] == 69
        assert board["overlaps"] == 1
        assert (await call(alice, "snapshot"))["overlaps"] == 0
        offset = 0
        all_claims: list[dict[str, Any]] = []
        while True:
            page = await call(orch, "claims", lane="bob", offset=offset)
            assert page["cursor"] == board["cursor"]
            all_claims.extend(page["claims"])
            if "next_offset" not in page:
                break
            assert page["next_offset"] > offset
            offset = page["next_offset"]
        assert [row["glob"] for row in all_claims] == [f"private/path-{n:02}" for n in range(68)]
        assert {row["reason"] for row in all_claims} == {"owned by bob"}
        for args in ({}, {"lane": "alice", "pr": 3}, {"pr": 0}):
            invalid = tool_json(await orch.call_tool("overlaps", args))
            assert invalid["error"]["code"] == "invalid"

        long_glob = "private/oversized-" + "x" * 10000
        long_reason = "Context " * 1500
        await call(
            orch,
            "claim",
            lane="bob",
            globs=[long_glob],
            reason=long_reason,
            request_id="long-reason",
        )
        page = await call(orch, "claims", lane="bob")
        assert page["claims"][0] == {"offset": 0, "details_omitted": True}
        assert page["claims"][1]["glob"] == "private/path-00"
        assert page["next_offset"] == len(page["claims"])
        detail = await call(orch, "claims", lane="bob", offset=0, detail=True)
        assert detail["claims"] == [{"lane": "bob", "glob": long_glob, "reason": long_reason}]
        assert (await call(alice, "claims", lane="bob", detail=True))["claims"] == []
        assert (await call(orch, "claims", lane="bob", offset=1))["claims"][0]["glob"] == (
            "private/path-00"
        )


async def test_worker_can_claim_only_own_lane(tmp_path: Path, repository: Repository) -> None:
    assert not repository.pulls
    async with running_daemon(tmp_path) as (_, orch, alice):
        await start(orch, "alice")
        await start(orch, "bob")
        await claim(alice, "alice", "src/alice")
        args = {"lane": "bob", "globs": ["src/bob"], "request_id": "write"}
        for command in ("claim", "release"):
            change = {**args, "reason": "attempt"} if command == "claim" else args
            denied = tool_json(await alice.call_tool(command, change))
            assert denied["error"]["code"] == "forbidden"
        negated = tool_json(
            await alice.call_tool(
                "claim",
                {"lane": "alice", "globs": ["!src/alice"], "reason": "negated", "request_id": "no"},
            )
        )
        assert negated["error"]["code"] == "invalid"
        await claim(orch, "bob", "src/bob")
        assert await call(orch, "release", **args) == {
            "claims": [{"lane": "bob", "glob": "src/bob", "reason": "owned by bob"}],
            "changed": True,
        }
        assert (await call(orch, "snapshot"))["claims"] == 1
