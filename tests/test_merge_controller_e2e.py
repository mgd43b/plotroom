"""Epic 4 acceptance against a daemon and a dedicated, in-memory GitHub repository."""

import asyncio
import json
import shlex
import subprocess
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.client.client import Client

from conftest import authenticated_client, tool_json
from fakes import FixedClock
from plotroom.adapters.github import GitHubPoller, github_secret_name
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.read import REVIEW_THREADS_QUERY
from plotroom.app.config import Config, GitHubConfig
from plotroom.app.serve import Daemon
from plotroom.app.tokens import TokenRegistry
from plotroom.core.events import Event
from plotroom.core.merge_flakes import MAIN_FLAKE_EVENT
from plotroom.core.merge_gate import GatePolicy, GateSnapshot

REPO = "fixture/epic4"
API = "https://epic4.invalid"
MAIN = "a" * 40
BASE = "b" * 40
OTHER = "c" * 40
NOW = "2026-11-22T12:00:00Z"
LATER = "2026-11-22T12:00:01Z"
WORKFLOW = "ci.yml"
FIX_LABEL = "main-fix"
ROOT = Path(__file__).resolve().parents[1]
SHELL_GATE = ROOT / ".claude/skills/clerk/scripts/safemerge.sh"
SHELL_LOOKUP = ROOT / "tests/fixtures/merge_gate_shell_reads.py"
type Row = dict[str, Any]


def status(identifier: int, name: str, conclusion: str | None) -> Row:
    return {
        "id": identifier,
        "name": name,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
    }


@dataclass
class Pull:
    number: int
    checks: tuple[str | None, ...] = ("success", "success")
    threads: int = 0
    path: str = "feature.py"
    body: str = ""
    message: str = ""
    labels: dict[str, str] = field(default_factory=dict[str, str])
    merge_commit: str | None = None
    unreadable: str = ""
    lane: str | None = None
    head_version: int = 0

    @property
    def head(self) -> str:
        return f"{self.number * 100 + self.head_version:040x}"

    def row(self) -> Row:
        updated = NOW if self.merge_commit is None and self.head_version == 0 else LATER
        return {
            "number": self.number,
            "state": "open" if self.merge_commit is None else "closed",
            "merged": self.merge_commit is not None,
            "merged_at": None if self.merge_commit is None else updated,
            "merge_commit_sha": self.merge_commit,
            "draft": False,
            "body": self.body,
            "updated_at": updated,
            "head": {
                "ref": self.lane or f"pr-{self.number}",
                "sha": self.head,
                "repo": {"full_name": REPO},
            },
            "base": {"ref": "main", "sha": MAIN, "repo": {"full_name": REPO}},
            "commits": 1,
            "changed_files": 1,
        }

    def commits(self) -> list[Row]:
        return [{"sha": self.head, "commit": {"message": self.message}}]


@dataclass(frozen=True)
class Run:
    identifier: int
    conclusion: str
    head: str
    at: str
    annotations: tuple[Row, ...] = ()
    job_conclusion: str | None = None

    def row(self) -> Row:
        return {
            **status(self.identifier, "ci", self.conclusion),
            "workflow_id": 1,
            "head_sha": self.head,
            "head_branch": "main",
            "repository": {"full_name": REPO},
            "head_repository": {"full_name": REPO},
            "event": "push",
            "run_attempt": 1,
            "created_at": self.at,
        }

    def jobs(self) -> list[Row]:
        return [
            {
                **status(self.identifier + 100, "tests", self.job_conclusion or self.conclusion),
                "check_run_url": f"{API}/repos/{REPO}/check-runs/{self.identifier + 200}",
            }
        ]

    def check(self) -> Row:
        return {
            **status(self.identifier + 200, "tests", self.job_conclusion or self.conclusion),
            "head_sha": self.head,
            "completed_at": self.at,
            "app": {"id": 7},
            "check_suite": {"id": 8},
            "output": {"annotations_count": len(self.annotations)},
        }


def file_change(path: str) -> Row:
    return {"filename": path, "status": "modified"}


def counted(key: str, rows: list[Row]) -> Row:
    return {"total_count": len(rows), key: rows}


@dataclass
class Repository:
    pulls: dict[int, Pull] = field(default_factory=dict[int, Pull])
    runs: list[Run] = field(default_factory=lambda: [Run(20, "success", MAIN, NOW)])
    main_paths: tuple[str, ...] = ("shared.py",)
    cycles: asyncio.Queue[asyncio.Future[None]] = field(
        default_factory=asyncio.Queue[asyncio.Future[None]]
    )

    def add(self, *pulls: Pull) -> None:
        self.pulls.update({pull.number: pull for pull in pulls})

    def execute_clerk_merge(self, command: str, pins: Row, verdict: Row) -> str:
        number, head = pins["pr"], pins["expected_head_sha"]
        if shlex.split(command) != [
            "gh",
            "pr",
            "merge",
            str(number),
            "--repo",
            REPO,
            "--merge",
            "--match-head-commit",
            head,
        ]:
            raise ValueError("unsupported fixture merge command")
        if verdict["verdict"] != "MERGE" or verdict["head_sha"] != head:
            raise ValueError("fixture merge requires a matching gate approval")
        pull = self.pulls[number]
        if pull.head != head or pull.merge_commit is not None:
            raise ValueError("fixture PR moved or is no longer open")
        pull.merge_commit = f"{number:040x}"
        return pull.merge_commit

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url.copy_with(path="", query=None)).rstrip("/") == API
        assert request.headers["authorization"] == "Bearer fixture"
        if request.url.path == "/graphql":
            assert request.method == "POST"
            query = json.loads(request.content)
            assert query["query"] == REVIEW_THREADS_QUERY
            variables = query["variables"]
            assert variables["owner"] == "fixture"
            assert variables["name"] == "epic4"
            assert variables["after"] is None
            pull = self.pulls[variables["number"]]
            if pull.unreadable == "threads":
                return httpx.Response(503)
            nodes = [
                {"id": f"thread-{index}", "isResolved": False, "path": pull.path, "line": 1}
                for index in range(pull.threads)
            ]
            connection = {
                "totalCount": len(nodes),
                "nodes": nodes,
                "pageInfo": {
                    "endCursor": None,
                    "hasNextPage": False,
                },
            }
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "headRefOid": pull.head,
                                "reviewThreads": connection,
                            }
                        }
                    }
                },
            )
        assert request.method == "GET", (request.method, request.url)
        prefix = f"/repos/{REPO}"
        assert request.url.path.startswith(prefix)
        path = request.url.path.removeprefix(prefix)
        assert request.url.params.get("page", "1") == "1"
        if path.startswith("/commits/") and path.endswith("/check-runs"):
            sha = path.split("/")[2]
            if pull := next((row for row in self.pulls.values() if row.head == sha), None):
                if pull.unreadable == "checks":
                    return httpx.Response(503)
                checks = [
                    {**status(pull.number * 10 + index, f"check-{index}", value), "head_sha": sha}
                    for index, value in enumerate(pull.checks)
                ]
            else:
                assert sha in {MAIN, *(run.head for run in self.runs)}, sha
                checks = [run.check() for run in self.runs if run.head == sha and run.annotations]
            return httpx.Response(200, json=counted("check_runs", checks))

        rows: dict[str, object] = {
            "": {"default_branch": "main"},
            "/branches/main": {"commit": {"sha": MAIN}},
            "/actions/runs": counted("workflow_runs", [run.row() for run in self.runs]),
            f"/actions/workflows/{WORKFLOW}/runs": counted(
                "workflow_runs",
                [run.row() for run in self.runs],
            ),
            "/pulls": [pull.row() for pull in self.pulls.values()],
            "/issues": [],
            "/issues/comments": [],
            f"/compare/{BASE}...{MAIN}": self.comparison(BASE, BASE, *self.main_paths),
        }
        for run in self.runs:
            rows[f"/actions/runs/{run.identifier}/attempts/1/jobs"] = counted("jobs", run.jobs())
            rows[f"/check-runs/{run.identifier + 200}/annotations"] = list(run.annotations)
            if run.head != MAIN:
                rows[f"/compare/{run.head}...{MAIN}"] = {
                    **self.comparison(run.head, run.head, *self.main_paths),
                    "commits": [{"sha": MAIN}],
                    "total_commits": 1,
                }
        for pull in self.pulls.values():
            rows.update(
                {
                    f"/pulls/{pull.number}": pull.row(),
                    f"/pulls/{pull.number}/files": [file_change(pull.path)],
                    f"/pulls/{pull.number}/commits": pull.commits(),
                    f"/issues/{pull.number}/labels": [{"name": label} for label in pull.labels],
                    f"/issues/{pull.number}/events": [
                        {
                            "id": i,
                            "event": "labeled",
                            "label": {"name": label},
                            "actor": {"login": actor, "type": "User"},
                        }
                        for i, (label, actor) in enumerate(pull.labels.items(), start=1)
                    ],
                    f"/compare/{pull.head}...{MAIN}": self.comparison(pull.head, BASE),
                    f"/compare/{BASE}...{pull.head}": self.comparison(BASE, BASE, pull.path),
                }
            )
            if pull.unreadable == "overlap" and path == f"/compare/{pull.head}...{MAIN}":
                return httpx.Response(503)
        assert path in rows, (request.method, request.url)
        return httpx.Response(200, json=rows[path])

    @staticmethod
    def comparison(base: str, merge_base: str, *paths: str) -> Row:
        return {
            "status": "ahead" if base == merge_base else "diverged",
            "base_commit": {"sha": base},
            "merge_base_commit": {"sha": merge_base},
            "files": [file_change(path) for path in paths],
        }

    async def refresh(self) -> None:
        finished = asyncio.get_running_loop().create_future()
        self.cycles.put_nowait(finished)
        await asyncio.wait_for(finished, 10)


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> Repository:
    fixture = Repository()
    monkeypatch.delenv(github_secret_name("epic4"), raising=False)

    async def drive(
        poller: GitHubPoller,
        after_pull: Callable[[httpx.AsyncClient], Awaitable[None]] | None = None,
    ) -> None:
        assert after_pull is not None
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            while True:
                finished = await fixture.cycles.get()
                try:
                    assert await poller.poll(client)
                    await after_pull(client)
                except Exception as exc:
                    finished.set_exception(exc)
                else:
                    finished.set_result(None)

    async def confirm_gate(
        poller: GitHubDetailPoller, snapshot: GateSnapshot
    ) -> GateSnapshot | None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            return await poller.read_gate_snapshot(client, snapshot)

    monkeypatch.setattr(GitHubPoller, "run", drive)
    monkeypatch.setattr(GitHubDetailPoller, "confirm_gate", confirm_gate)
    return fixture


@asynccontextmanager
async def running_daemon(directory: Path) -> AsyncGenerator[tuple[Daemon, Client, Client, Client]]:
    secret = directory / "token"
    secret.write_text("fixture", encoding="utf-8")
    config = Config(
        directory / "data",
        "127.0.0.1",
        0,
        ("127.0.0.1:*",),
        ("epic4",),
        workers={"epic4": frozenset({"worker"})},
        watchers={"epic4": ("clerk",)},
        next_budget=16000,
        github={
            "epic4": GitHubConfig(
                REPO,
                secret,
                API,
                60,
                merge_gate_policy=GatePolicy(
                    2,
                    ignore_checks=("check-2",),
                    ci_workflow=WORKFLOW,
                    fix_label=FIX_LABEL,
                    skip_label="skip-merge",
                    authorized_label_logins=("owner",),
                ),
            )
        },
    )
    tokens = TokenRegistry(config.data_dir)
    orch = tokens.issue("epic4", "orchestrator", local=False)
    clerk = tokens.issue("epic4", "clerk", local=False)
    worker = tokens.issue("epic4", "worker:worker", local=False)
    daemon = Daemon(config, clock=FixedClock(datetime.fromisoformat(LATER)))
    task = asyncio.create_task(daemon.run())
    try:
        port = await asyncio.wait_for(daemon.wait_started(), 10)
        url = f"http://127.0.0.1:{port}/mcp"
        async with (
            authenticated_client(url, orch.bearer) as orchestrator,
            authenticated_client(url, clerk.bearer) as reviewer,
            authenticated_client(url, worker.bearer) as lane_worker,
        ):
            yield daemon, orchestrator, reviewer, lane_worker
    finally:
        daemon.stop()
        await asyncio.wait_for(task, 10)


async def call(client: Client, name: str, **arguments: Any) -> Row:
    result = tool_json(await client.call_tool(name, arguments))
    assert result["ok"], result
    return result["data"]


async def events(daemon: Daemon, kind: str) -> list[Event]:
    ledger = daemon.ledgers["epic4"]
    matches: list[Event] = []
    cursor = 0
    while page := await ledger.events_after(cursor):
        matches.extend(event for event in page if event.kind == kind)
        cursor = page[-1].seq
    return matches


async def next_items(client: Client, kind: str) -> list[Row]:
    return [item for item in (await call(client, "next"))["items"] if item["kind"] == kind]


def rule_cases() -> list[tuple[Pull, str | None, int, str]]:
    return [
        (Pull(1), None, 0, "would merge"),
        (Pull(2, checks=(None, "failure"), threads=1), "checks_pending", 2, "1 checks pending"),
        (Pull(3, checks=("success",)), "checks_below_minimum", 2, "only 1 checks have reported"),
        (Pull(4, checks=("success", "failure")), "checks_failing", 2, "failing: check-1"),
        (Pull(5, unreadable="checks"), "checks_unreadable", 2, "could not read the checks"),
        (Pull(6, threads=2), "review_threads_unresolved", 2, "2 unresolved threads"),
        (
            Pull(7, unreadable="threads"),
            "review_threads_incomplete",
            2,
            "could not read the review threads",
        ),
        (Pull(8, path="shared.py", body="Fixes #91"), "stale_ci", 3, "main changed these PR files"),
        (Pull(9, unreadable="overlap"), "overlap_unreadable", 2, "no merge base"),
        (Pull(10, body="Fixes #91"), "closing_keywords", 4, "closing keywords"),
        (Pull(11, message="Resolves #92"), "closing_keywords", 4, "closing keywords"),
        (Pull(12, checks=("cancelled", "skipped", "failure")), None, 0, "would merge"),
        (Pull(14, body="éfix #2"), "closing_keywords", 4, "closing keywords"),
    ]


def shell_reads(repository: Repository, pull: Pull) -> list[tuple[list[str], str, int]]:
    actions: list[tuple[list[str], str, int]] = []

    def read(args: list[str], response: str = "", exit_code: int = 0) -> None:
        actions.append((args, response, exit_code))

    number = str(pull.number)
    read(["git", "remote", "get-url", "origin"], f"./{REPO}.git\n")
    read(["git", "fetch", "-q", "origin"])
    read(
        ["gh", "pr", "view", number, "--json", "headRefOid", "--jq", ".headRefOid"],
        pull.head + "\n",
    )
    read(["git", "fetch", "-q", "origin", f"pull/{number}/head"])
    checks = [str(pull.checks.count(None)), str(len(pull.checks))]
    checks.extend(f"check-{i}" for i, value in enumerate(pull.checks) if value == "failure")
    read(
        [
            "gh",
            "pr",
            "checks",
            number,
            "--json",
            "name,bucket",
            "--jq",
            '([.[]|select(.bucket=="pending")]|length), length, (.[]|select(.bucket=="fail")|.name)',
        ],
        "" if pull.unreadable == "checks" else "\n".join(checks) + "\n",
    )
    query = (
        'query{repository(owner:"fixture",name:"epic4"){pullRequest(number:'
        + number
        + "){reviewThreads(first:100){totalCount nodes{isResolved}}}}}"
    )
    read(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "--jq",
            'if .data.repository.pullRequest.reviewThreads.totalCount > 100 then "many" else ([.data.repository.pullRequest.reviewThreads.nodes[]|select(.isResolved==false)]|length) end',
        ],
        "" if pull.unreadable == "threads" else f"{pull.threads}\n",
    )
    read(
        [
            "gh",
            "run",
            "list",
            "--branch",
            "main",
            "--workflow",
            WORKFLOW,
            "--status",
            "completed",
            "--limit",
            "30",
            "--json",
            "databaseId,conclusion,headSha",
            "--jq",
            r'.[]|select(.conclusion!="cancelled" and .conclusion!="skipped")|"\(.databaseId) \(.conclusion) \(.headSha[0:8])"',
        ],
        "".join(f"{run.identifier} {run.conclusion} {run.head[:8]}\n" for run in repository.runs),
    )
    for run in repository.runs:
        read(
            [
                "gh",
                "run",
                "view",
                str(run.identifier),
                "--json",
                "jobs",
                "--jq",
                '[.jobs[]|select(.conclusion=="failure" or .conclusion=="timed_out")]|length',
            ],
            f"{int(run.conclusion in {'failure', 'timed_out'})}\n",
        )
    read(
        ["git", "merge-base", pull.head, "origin/main"],
        "" if pull.unreadable == "overlap" else f"{BASE}\n",
        int(pull.unreadable == "overlap"),
    )
    read(["git", "diff", "--name-only", BASE, pull.head], pull.path + "\n")
    read(
        ["git", "diff", "--name-only", BASE, "origin/main"], "\n".join(repository.main_paths) + "\n"
    )
    read(["gh", "pr", "view", number, "--json", "body", "--jq", ".body"], pull.body + "\n")
    read(["git", "log", "--format=%B", f"{BASE}..{pull.head}"], pull.message + "\n")
    return actions


def shell_gate(
    directory: Path,
    repository: Repository,
    pull: Pull,
    *,
    expected_head: str | None = None,
) -> tuple[int, str]:
    case = directory / "shell"
    case.mkdir(exist_ok=True)
    inputs = case / "reads.json"
    inputs.write_text(json.dumps(shell_reads(repository, pull)), encoding="utf-8")
    audit = case / "audit.jsonl"
    audit.unlink(missing_ok=True)
    for command in ("gh", "git"):
        shim = case / command
        argv = shlex.join([sys.executable, str(SHELL_LOOKUP), command])
        shim.write_text(f'#!/bin/bash\nexec {argv} "$@"\n', encoding="utf-8")
        shim.chmod(0o700)
    for utility in ("awk", "comm", "dirname", "grep", "sed", "sort", "tr"):
        (case / utility).unlink(missing_ok=True)
        (case / utility).symlink_to(f"/usr/bin/{utility}")
    result = subprocess.run(
        ["/bin/bash", str(SHELL_GATE), str(pull.number)],
        env={
            "PATH": str(case),
            "LC_ALL": "C",
            "ORCH_ROOT": str(case),
            "ORCH_REPO": REPO,
            "ORCH_BASE_BRANCH": "main",
            "ORCH_CI_WORKFLOW": WORKFLOW,
            "ORCH_MIN_CHECKS": "2",
            "ORCH_IGNORE_CHECKS": "check-2",
            "ORCH_EXPECT_HEAD": expected_head or pull.head,
            "ORCH_DRY": "1",
            "ORCH_STATE_DIR": str(case / "state"),
            "GATE_FIXTURE_RESPONSES": str(inputs),
            "GATE_FIXTURE_AUDIT": str(audit),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    calls = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert all(call["matched"] for call in calls), calls
    assert not result.stderr, result.stderr
    assert not (case / "state").exists()
    return result.returncode, result.stdout.splitlines()[0]


async def test_dry_and_live_gate_agree_on_all_six_rules(
    tmp_path: Path,
    repository: Repository,
) -> None:
    cases = rule_cases()
    red = Pull(13)
    repository.add(*(pull for pull, *_ in cases), red)
    async with running_daemon(tmp_path) as (daemon, orch, clerk, _):
        await repository.refresh()
        await repository.refresh()  # The detail poller reads eight PRs per pass.
        for pull, reason, exit_code, _ in cases:
            dry = await call(clerk, "merge_gate", pr=pull.number)
            assert dry["exit_class"] == exit_code, dry
            assert dry["verdict"] == ("MERGE" if exit_code == 0 else "REFUSE")
            assert [entry["code"] for entry in dry["reasons"]] == (
                [] if reason is None else [reason]
            )
            await assert_live_verdict(daemon, pull, dry)
        refused_numbers = {
            item["context"]["number"] for item in await next_items(orch, "merge_refusal")
        }
        assert {2, 3}.isdisjoint(refused_numbers)

        moved = cases[0][0]
        expected = moved.head
        moved.head_version += 1
        await repository.refresh()
        current = await call(clerk, "merge_gate", pr=moved.number)
        await assert_live_verdict(daemon, moved, current)
        assert any(
            event.fields.get("supersedes_head_sha") == expected
            and event.fields["head_sha"] == moved.head
            for event in await events(daemon, "core.merge_verdict")
        )
        pinned = await call(clerk, "merge_gate", pr=moved.number, expected_head_sha=expected)
        assert pinned["verdict"] == "REFUSE"
        assert "head moved" in pinned["reasons"][0]["detail"]
        stale_main = await call(
            clerk,
            "merge_gate",
            pr=moved.number,
            expected_head_sha=moved.head,
            expected_main_sha=OTHER,
        )
        assert stale_main["verdict"] == "REFUSE"
        assert stale_main["reasons"][0]["code"] == "main_moved"

        await call(orch, "freeze", reason="fixture deploy", request_id="freeze")
        await repository.refresh()
        frozen = await call(clerk, "merge_gate", pr=moved.number)
        assert [reason["code"] for reason in frozen["reasons"]] == ["deploy_window"]
        await assert_live_verdict(daemon, moved, frozen)
        await call(orch, "unfreeze", request_id="unfreeze")
        repository.runs = [Run(21, "failure", MAIN, LATER)]
        await repository.refresh()
        dry = await call(clerk, "merge_gate", pr=red.number)
        assert [reason["code"] for reason in dry["reasons"]] == ["main_red"]
        await assert_live_verdict(daemon, red, dry)


async def assert_live_verdict(daemon: Daemon, pull: Pull, dry: Row) -> None:
    live = [
        event.fields
        for event in await events(daemon, "core.merge_verdict")
        if event.fields["number"] == pull.number and event.fields["head_sha"] == pull.head
    ]
    assert live, pull.number
    assert {key: live[-1][key] for key in dry} == dry
    assert dry["head_sha"] == pull.head
    assert dry["as_of"] is not None


async def test_clerk_next_and_observed_merges(
    tmp_path: Path,
    repository: Repository,
) -> None:
    ready = Pull(101, lane="feature")
    refused = Pull(102, threads=1, lane="needs-review")
    skipped = Pull(103, labels={"skip-merge": "owner"}, threads=1)
    repository.add(ready, refused, skipped)
    async with running_daemon(tmp_path) as (daemon, orch, clerk, worker):
        await call(
            orch,
            "lane_start",
            lane=refused.lane,
            worker="worker",
            branch=refused.lane,
            work_items=[],
            request_id="start-refused",
        )
        cursor = (await call(worker, "wait", timeout=0))["cursor"]
        await repository.refresh()
        first = await next_items(clerk, "merge_ready")
        assert len(first) == 1, first
        assert (await call(clerk, "merge_gate", pr=skipped.number))["reasons"][0]["code"] == (
            "skip_label"
        )
        assert all(
            event.fields["number"] != skipped.number
            for event in await events(daemon, "core.merge_verdict")
        )
        await repository.refresh()
        assert await next_items(clerk, "merge_ready") == first
        position = cursor
        while True:
            result = await call(worker, "wait", timeout=0, cursor=position)
            item = result["item"]
            assert item is not None, "the owning worker did not receive the refusal"
            assert result["cursor"] > position
            position = result["cursor"]
            if (
                item["kind"] == "merge_refusal"
                and item["state"] == "open"
                and item["context"]["reason"]["code"] == "review_threads_unresolved"
            ):
                assert item["context"]["number"] == refused.number
                break

        refused_events = [
            event
            for event in await events(daemon, "core.merge_refusal_reported")
            if event.fields["number"] == refused.number
            and event.fields["head_sha"] == refused.head
            and event.fields["reason"]["code"] == "review_threads_unresolved"
        ]
        assert len(refused_events) == 1

        [offer] = first
        args = offer["context"]["merge_gate"]
        assert args == {
            "pr": ready.number,
            "expected_head_sha": ready.head,
            "expected_main_sha": MAIN,
        }
        await call(orch, "freeze", reason="fixture deploy", request_id="freeze")
        assert await next_items(clerk, "merge_ready") == []
        dry = await call(clerk, "merge_gate", **args)
        assert dry["reasons"][0]["code"] == "deploy_window"
        await call(orch, "unfreeze", request_id="unfreeze")
        assert len(await next_items(clerk, "merge_ready")) == 1

        ready.threads = 1
        projected = await call(clerk, "merge_gate", pr=ready.number)
        assert projected["verdict"] == "MERGE"
        authoritative = await call(clerk, "merge_gate", **args)
        assert authoritative["reasons"][0]["code"] == "review_threads_unresolved"
        await repository.refresh()
        assert await next_items(clerk, "merge_ready") == []
        dry = await call(clerk, "merge_gate", **args)
        assert dry["reasons"][0]["code"] == "review_threads_unresolved"
        ready.threads = 0
        await repository.refresh()
        (restored,) = await next_items(clerk, "merge_ready")
        assert restored["context"]["merge_gate"] == args
        pinned = await call(clerk, "merge_gate", **restored["context"]["merge_gate"])
        assert pinned["verdict"] == "MERGE"
        assert await daemon.ledgers["epic4"].pending_action() is None
        command = restored["context"]["command"]
        with pytest.raises(ValueError, match="unsupported fixture merge command"):
            repository.execute_clerk_merge("gh pr merge", args, pinned)
        ready.head_version += 1
        with pytest.raises(ValueError, match="fixture PR moved"):
            repository.execute_clerk_merge(command, args, pinned)
        assert ready.merge_commit is None
        ready.head_version -= 1
        pinned = await call(clerk, "merge_gate", **args)
        merged_sha = repository.execute_clerk_merge(command, args, pinned)
        # A hand merge of a refused PR has no matching gate approval.
        refused.merge_commit = "e" * 40
        await repository.refresh()
        await repository.refresh()
        observed = await events(daemon, "core.merge_observed")
        assert {
            event.fields["number"]: (event.fields["classification"], event.fields["merge_sha"])
            for event in observed
        } == {101: ("gated", merged_sha), 102: ("outside_gate", refused.merge_commit)}
        assert len(observed) == 2
        assert await next_items(clerk, "merge_ready") == []
        (outside,) = await next_items(orch, "outside_gate_merge")
        assert outside["context"]["number"] == refused.number


async def test_red_main_holds_except_fix_and_main_flake_does_not_freeze(
    tmp_path: Path,
    repository: Repository,
) -> None:
    ordinary = Pull(201)
    fix = Pull(202, labels={FIX_LABEL: "owner"})
    impostor = Pull(203, labels={FIX_LABEL: "untrusted"})
    repository.add(ordinary, fix, impostor)
    failed = Run(
        21,
        "failure",
        OTHER,
        NOW,
        (
            {
                "annotation_level": "failure",
                "path": "tests/test_main.py",
                "title": "test_main",
                "message": "fixture assertion failed",
            },
        ),
    )
    passed = Run(
        23,
        "cancelled",
        MAIN,
        LATER,
        (
            {
                "annotation_level": "notice",
                "path": "tests/test_main.py",
                "title": "test_main",
                "message": "plotroom:test-outcome=passed",
            },
        ),
        job_conclusion="success",
    )
    unproven = replace(
        passed,
        identifier=22,
        annotations=({**passed.annotations[0], "message": "fixture test output"},),
    )
    older_green = Run(20, "success", BASE, NOW)
    repository.runs = [failed, older_green]
    async with running_daemon(tmp_path) as (daemon, orch, clerk, _):
        await repository.refresh()
        assert (await call(clerk, "merge_gate", pr=ordinary.number))["reasons"][0][
            "code"
        ] == "main_red"
        assert (await call(clerk, "merge_gate", pr=impostor.number))["reasons"][0][
            "code"
        ] == "main_red"
        assert (await call(orch, "merge_gate", pr=fix.number))["verdict"] == "MERGE"
        assert await next_items(clerk, "merge_ready") == []
        (offered_fix,) = await next_items(orch, "merge_ready")
        assert offered_fix["context"]["number"] == fix.number
        assert await next_items(clerk, "flake_candidate") == []

        # A completed test in a cancelled workflow proves the flake without setting main green.
        repository.runs = [unproven, failed, older_green]
        await repository.refresh()
        assert (await call(clerk, "merge_gate", pr=ordinary.number))["reasons"][0][
            "code"
        ] == "main_red"
        assert await next_items(clerk, "flake_candidate") == []
        repository.runs = [passed, failed, older_green]
        await repository.refresh()
        (flake,) = await next_items(clerk, "flake_candidate")
        assert flake["context"]["head_sha"] == failed.head
        assert flake["context"]["check_id"] == failed.identifier + 200
        assert flake["context"]["test_id"] == "test_main"
        assert "fixture assertion failed" in flake["context"]["excerpt"]
        (proof,) = await events(daemon, MAIN_FLAKE_EVENT)
        assert proof.fields["head_sha"] == flake["context"]["head_sha"]
        assert proof.fields["check_id"] == flake["context"]["check_id"]
        assert (await call(clerk, "merge_gate", pr=ordinary.number))["verdict"] == "MERGE"
        assert not (await call(orch, "next")).get("freezes")


async def test_projection_and_clerk_shell_agree_on_fixture_repository(
    tmp_path: Path,
    repository: Repository,
) -> None:
    cases = rule_cases()
    repository.add(*(pull for pull, *_ in cases))
    async with running_daemon(tmp_path) as (_, _, clerk, _):
        await repository.refresh()
        await repository.refresh()  # The detail poller reads eight PRs per pass.
        for pull, reason, _, fragment in cases:
            exit_code, first_line = await asyncio.to_thread(shell_gate, tmp_path, repository, pull)
            dry = await call(clerk, "merge_gate", pr=pull.number, expected_head_sha=pull.head)
            assert dry["verdict"] == ("MERGE" if exit_code == 0 else "REFUSE"), first_line
            assert dry["exit_class"] == exit_code
            assert fragment in first_line
            if reason is not None:
                assert reason in {entry["code"] for entry in dry["reasons"]}, first_line

        pull = cases[0][0]
        exit_code, first_line = await asyncio.to_thread(
            shell_gate,
            tmp_path,
            repository,
            pull,
            expected_head=OTHER,
        )
        dry = await call(clerk, "merge_gate", pr=pull.number, expected_head_sha=OTHER)
        assert exit_code == dry["exit_class"] == 2
        assert dry["verdict"] == "REFUSE"
        assert "head moved" in first_line
        assert "head moved" in dry["reasons"][0]["detail"]

        for runs, reason, expected_exit, fragment in (
            ([Run(21, "failure", MAIN, LATER)], "main_red", 5, "latest CI verdict is red"),
            ([], "main_no_verdict", 2, "has no usable CI verdict"),
        ):
            repository.runs = runs
            await repository.refresh()
            exit_code, first_line = await asyncio.to_thread(shell_gate, tmp_path, repository, pull)
            dry = await call(clerk, "merge_gate", pr=pull.number)
            assert dry["verdict"] == "REFUSE"
            assert exit_code == dry["exit_class"] == expected_exit
            assert fragment in first_line
            assert reason in {entry["code"] for entry in dry["reasons"]}
