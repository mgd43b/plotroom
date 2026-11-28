"""Epic 4 acceptance against a daemon and a dedicated, in-memory GitHub repository."""

import asyncio
import json
import shlex
import shutil
import subprocess
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.client.client import Client

from conftest import authenticated_client, tool_json
from fakes import FixedClock
from plotroom.adapters.github import GitHubPoller, github_secret_name
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.pr_details import fetch_pull_text
from plotroom.adapters.github.read import (
    CADENCE_THREADS_QUERY,
    REVIEW_THREADS_QUERY,
    GitHubReadClient,
)
from plotroom.app.config import Config, GitHubConfig
from plotroom.app.serve import Daemon
from plotroom.app.tokens import TokenRegistry
from plotroom.core.events import Event, format_time
from plotroom.core.github import PULL_DETAIL_ATTEMPT_PREFIX, PullRequest
from plotroom.core.github_projection import PULL_TEXT, GitHubResource
from plotroom.core.kodus_review import KodusReviewPolicy
from plotroom.core.merge_flakes import MAIN_FLAKE_EVENT
from plotroom.core.merge_gate import GatePolicy, GateSnapshot
from plotroom.core.unit_of_work import UnitOfWork

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
    closed: bool = False

    @property
    def head(self) -> str:
        return f"{self.number * 100 + self.head_version:040x}"

    def row(self) -> Row:
        updated = NOW if self.merge_commit is None and self.head_version == 0 else LATER
        return {
            "number": self.number,
            "id": self.number + 1_000_000,
            "node_id": f"PR_{self.number}",
            "user": {"login": "developer"},
            "created_at": NOW,
            "state": "closed" if self.closed or self.merge_commit is not None else "open",
            "merged": self.merge_commit is not None,
            "merged_at": None if self.merge_commit is None else updated,
            "merge_commit_sha": self.merge_commit,
            "draft": False,
            "body": self.body,
            "updated_at": updated,
            "head": {
                "ref": self.lane or f"pr-{self.number}",
                "sha": self.head,
                "repo": {"id": 55, "full_name": REPO},
            },
            "base": {"ref": "main", "sha": MAIN, "repo": {"id": 55, "full_name": REPO}},
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
    main_head: str = MAIN
    main_reads: list[str] = field(default_factory=list[str])
    statuses: dict[str, list[Row] | int] = field(default_factory=dict[str, list[Row] | int])
    review_runs: dict[str, list[Row]] = field(default_factory=dict[str, list[Row]])
    cadence_threads: dict[int, list[Row]] = field(default_factory=dict[int, list[Row]])
    thread_reads: list[int] = field(default_factory=list[int])
    request_clock: FixedClock | None = None
    request_count: int = 0
    last_head_rate_limit: int | None = None
    cycles: asyncio.Queue[tuple[asyncio.Future[None], bool]] = field(
        default_factory=asyncio.Queue[tuple[asyncio.Future[None], bool]]
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
        if pins["expected_main_sha"] != self.main_head:
            raise ValueError("fixture main moved")
        pull = self.pulls[number]
        if pull.head != head or pull.merge_commit is not None:
            raise ValueError("fixture PR moved or is no longer open")
        pull.merge_commit = f"{number:040x}"
        return pull.merge_commit

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        if self.request_clock is not None:
            self.request_clock.advance(0.006)
        assert str(request.url.copy_with(path="", query=None)).rstrip("/") == API
        assert request.headers["authorization"] == "Bearer fixture"
        if request.url.path == "/graphql":
            assert request.method == "POST"
            query = json.loads(request.content)
            assert query["query"] in (REVIEW_THREADS_QUERY, CADENCE_THREADS_QUERY)
            variables = query["variables"]
            assert variables["owner"] == "fixture"
            assert variables["name"] == "epic4"
            assert variables["after"] is None
            pull = self.pulls[variables["number"]]
            if query["query"] == REVIEW_THREADS_QUERY:
                self.thread_reads.append(pull.number)
            if pull.unreadable == "rate-limited":
                return httpx.Response(429, headers={"Retry-After": "20"})
            if pull.unreadable == "threads":
                return httpx.Response(503)
            if pull.number in self.cadence_threads:
                nodes = [
                    {**thread, "path": pull.path, "line": 1}
                    for thread in self.cadence_threads[pull.number]
                ]
            else:
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
                            "nameWithOwner": REPO,
                            "pullRequest": {
                                "id": pull.row()["node_id"],
                                "number": pull.number,
                                "author": pull.row()["user"],
                                "headRefOid": pull.head,
                                "reviewThreads": connection,
                            },
                        }
                    }
                },
            )
        assert request.method == "GET", (request.method, request.url)
        prefix = f"/repos/{REPO}"
        assert request.url.path.startswith(prefix)
        path = request.url.path.removeprefix(prefix)
        assert request.url.params.get("page", "1") == "1"
        if path == "/branches/main":
            tip = self.main_reads.pop(0) if self.main_reads else self.main_head
            return httpx.Response(200, json={"commit": {"sha": tip}})
        if path.startswith("/commits/") and path.endswith("/statuses"):
            sha = path.split("/")[2]
            assert any(pull.head == sha for pull in self.pulls.values()), sha
            result = self.statuses.get(sha, [])
            return (
                httpx.Response(result)
                if isinstance(result, int)
                else httpx.Response(200, json=result)
            )
        if path.startswith("/commits/") and path.endswith("/check-runs"):
            sha = path.split("/")[2]
            if pull := next((row for row in self.pulls.values() if row.head == sha), None):
                if pull.unreadable == "rate-limited-checks":
                    self.last_head_rate_limit = self.request_count
                    return httpx.Response(429, headers={"Retry-After": "20"})
                if pull.unreadable == "checks":
                    return httpx.Response(503)
                checks = [
                    {**status(pull.number * 10 + index, f"check-{index}", value), "head_sha": sha}
                    for index, value in enumerate(pull.checks)
                ]
            else:
                assert sha in {MAIN, *(run.head for run in self.runs), *self.review_runs}, sha
                checks = [run.check() for run in self.runs if run.head == sha and run.annotations]
            checks.extend(self.review_runs.get(sha, []))
            if check_name := request.url.params.get("check_name"):
                checks = [check for check in checks if check["name"] == check_name]
            return httpx.Response(200, json=counted("check_runs", checks))

        rows: dict[str, object] = {
            "/deployments": [],
            "": {"default_branch": "main"},
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
            if pull.unreadable == "text" and path == f"/pulls/{pull.number}/commits":
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

    async def refresh(self, *, expect_commit: bool = True) -> None:
        finished = asyncio.get_running_loop().create_future()
        self.cycles.put_nowait((finished, expect_commit))
        # The test clock owns evidence age. Allow a full 19-target local SQLite pass.
        await asyncio.wait_for(finished, 30)


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
                finished, expect_commit = await fixture.cycles.get()
                try:
                    committed = await poller.poll(client)
                    assert committed == expect_commit
                    if committed:
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
async def running_daemon(
    directory: Path,
    *,
    kodus_review: KodusReviewPolicy | None = None,
    max_age: timedelta = timedelta(minutes=2),
    clock: FixedClock | None = None,
    poll_seconds: int = 60,
) -> AsyncGenerator[tuple[Daemon, Client, Client, Client]]:
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
                poll_seconds,
                merge_gate_policy=GatePolicy(
                    2,
                    ignore_checks=("check-2",),
                    max_age=max_age,
                    ci_workflow=WORKFLOW,
                    fix_label=FIX_LABEL,
                    skip_label="skip-merge",
                    authorized_label_logins=("owner",),
                    kodus_review=kodus_review,
                ),
            )
        },
    )
    tokens = TokenRegistry(config.data_dir)
    orch = tokens.issue("epic4", "orchestrator", local=False)
    clerk = tokens.issue("epic4", "clerk", local=False)
    worker = tokens.issue("epic4", "worker:worker", local=False)
    daemon = Daemon(config, clock=clock or FixedClock(datetime.fromisoformat(LATER)))
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

    def api_read(path: str, payload: Row) -> None:
        read(["gh", "api", path], json.dumps(payload))

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
    # Supply the bootstrap gate's additional proof so parity measures the shared rules.
    run_id = pull.number
    api_read(
        f"repos/{REPO}/actions/workflows/{WORKFLOW}/runs"
        f"?event=pull_request&head_sha={pull.head}&per_page=100",
        counted(
            "workflow_runs",
            [
                {
                    **status(run_id, "ci", "success"),
                    "head_sha": pull.head,
                    "event": "pull_request",
                    "created_at": NOW,
                    "run_attempt": 1,
                    "pull_requests": [{"number": pull.number}],
                }
            ],
        ),
    )
    api_read(
        f"repos/{REPO}/actions/runs/{run_id}/attempts/1/jobs?per_page=100",
        counted(
            "jobs",
            [
                {
                    **status(job_id, f"check (python {version})", "success"),
                    "head_sha": pull.head,
                    "run_id": run_id,
                    "run_attempt": 1,
                }
                for job_id, version in enumerate(("3.14", "3.15"), start=1)
            ],
        ),
    )
    api_read(
        f"repos/{REPO}/commits/{pull.head}/check-runs"
        "?check_name=Kody%20Code%20Review&filter=latest&per_page=100",
        counted(
            "check_runs",
            [
                {
                    **status(1, "Kody Code Review", "success"),
                    "head_sha": pull.head,
                    "app": {"id": 5822892, "slug": "kodus-mgd43b"},
                    "pull_requests": [{"number": pull.number}],
                    "output": {
                        "summary": "Review finished successfully. Suggestions (if any) "
                        "were posted as PR/file comments."
                    },
                }
            ],
        ),
    )
    query = """query($owner: String!, $repo: String!, $pr: Int!) {
    repository(owner: $owner, name: $repo) {
      pullRequest(number: $pr) {
        number headRefOid author { login }
        reviewThreads(first: 100) {
          totalCount pageInfo { hasNextPage }
          nodes { isResolved comments(first: 10) { totalCount pageInfo { hasNextPage } nodes { author { __typename login } authorAssociation } } }
        }
      }
    }
  }"""
    read(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            "owner=fixture",
            "-f",
            "repo=epic4",
            "-F",
            f"pr={number}",
        ],
        ""
        if pull.unreadable == "threads"
        else json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "number": pull.number,
                            "headRefOid": pull.head,
                            "reviewThreads": {
                                "totalCount": pull.threads,
                                "pageInfo": {"hasNextPage": False},
                                "nodes": [{"isResolved": False} for _ in range(pull.threads)],
                            },
                        }
                    }
                }
            }
        ),
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
    jq = shutil.which("jq")
    assert jq is not None, "the clerk gate needs jq"
    (case / "jq").unlink(missing_ok=True)
    (case / "jq").symlink_to(jq)
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


async def test_legacy_statuses_update_live_and_refresh_at_pinned_confirmation(
    tmp_path: Path, repository: Repository
) -> None:
    pull = Pull(1, checks=("success", "failure"))
    repository.add(pull)

    def legacy(identifier: int, state: str) -> Row:
        return {
            "id": identifier,
            "context": "external",
            "state": state,
            "creator": {"id": 7},
            "url": f"{API}/repos/{REPO}/statuses/{pull.head}",
        }

    repository.statuses[pull.head] = [legacy(1, "success")]
    async with running_daemon(tmp_path) as (daemon, _, clerk, _):
        await repository.refresh()
        dry = await call(clerk, "merge_gate", pr=pull.number)
        assert dry["reasons"][0]["detail"] == "failing: check-1"
        await assert_live_verdict(daemon, pull, dry)

        # Replacing Check Runs alone would combine two successes and stage MERGE.
        pull.checks = ("success", "success")
        repository.statuses[pull.head] = [legacy(2, "failure"), legacy(1, "success")]
        await repository.refresh()
        dry = await call(clerk, "merge_gate", pr=pull.number)
        assert dry["reasons"][0]["detail"] == "failing: external"
        await assert_live_verdict(daemon, pull, dry)
        assert await next_items(clerk, "merge_ready") == []
        assert all(
            event.fields["verdict"] == "REFUSE"
            for event in await events(daemon, "core.merge_verdict")
        )

        repository.statuses[pull.head] = [legacy(3, "success"), legacy(2, "failure")]
        await repository.refresh()
        [offer] = await next_items(clerk, "merge_ready")
        pins = offer["context"]["merge_gate"]
        assert await call(clerk, "merge_gate", **pins) == await call(clerk, "merge_gate", pr=1)

        repository.statuses[pull.head] = [legacy(4, "pending")]
        assert (await call(clerk, "merge_gate", pr=1))["verdict"] == "MERGE"
        authoritative = await call(clerk, "merge_gate", **pins)
        assert authoritative["reasons"][0]["code"] == "checks_pending"
        await repository.refresh()
        dry = await call(clerk, "merge_gate", pr=1)
        assert dry == authoritative
        await assert_live_verdict(daemon, pull, dry)
        assert await next_items(clerk, "merge_ready") == []

        repository.statuses[pull.head] = []
        await repository.refresh()
        assert (await call(clerk, "merge_gate", pr=1))["verdict"] == "MERGE"
        repository.statuses[pull.head] = 403
        denied = await call(clerk, "merge_gate", **pins)
        assert denied["reasons"][0]["code"] == "commit_statuses_unreadable"
        await repository.refresh()
        dry = await call(clerk, "merge_gate", pr=1)
        assert dry == denied
        await assert_live_verdict(daemon, pull, dry)
        assert await next_items(clerk, "merge_ready") == []


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
        assert restored["context"]["command_pin"] == "head_sha"
        repository.main_reads = [MAIN, MAIN, OTHER]
        moved_during_read = await call(clerk, "merge_gate", **args)
        assert moved_during_read["verdict"] == "REFUSE"
        assert repository.main_reads == []
        repository.main_head = OTHER
        with pytest.raises(ValueError, match="fixture main moved"):
            repository.execute_clerk_merge(command, args, pinned)
        repository.main_head = MAIN
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


async def test_seventeen_pull_details_remain_fresh_and_overload_retracts_offers(
    tmp_path: Path, repository: Repository
) -> None:
    clock = FixedClock(datetime.fromisoformat(LATER))
    repository.request_clock = clock
    active = [Pull(number) for number in range(1, 18)]
    repository.add(*active, *(Pull(number, closed=True) for number in range(101, 105)))

    async with running_daemon(
        tmp_path, clock=clock, poll_seconds=10, max_age=timedelta(seconds=20)
    ) as (
        daemon,
        _,
        clerk,
        _,
    ):
        ledger = daemon.ledgers["epic4"]
        for terminals in ([101, 102], [103, 104], []):
            repository.thread_reads.clear()
            started = clock.now()
            await repository.refresh()
            assert timedelta() < clock.now() - started <= timedelta(seconds=5)
            assert repository.thread_reads == [*range(1, 18), *terminals]
            # Check at the end of the sleep, before the following pass renews anything.
            clock.advance(10)
            for pull in active:
                assert (await ledger.merge_gate("clerk", pull.number)).verdict == "MERGE"
        assert len(await next_items(clerk, "merge_ready")) == 17

        repository.add(Pull(18))
        await repository.refresh()
        assert await next_items(clerk, "merge_ready") == []
        for head_pin in (None, active[0].head):
            verdict = await ledger.merge_gate(
                "clerk",
                1,
                expected_head_sha=head_pin,
                expected_main_sha=MAIN if head_pin is not None else None,
            )
            assert [reason.code for reason in verdict.reasons] == ["detail_capacity_exceeded"]

        repository.pulls[18].closed = True
        active[0].unreadable = "threads"
        clock.advance(10)
        await repository.refresh()
        verdict = await ledger.merge_gate("clerk", 1)
        assert [reason.code for reason in verdict.reasons] == ["review_threads_incomplete"]
        assert {
            item["context"]["number"] for item in await next_items(clerk, "merge_ready")
        } == set(range(2, 18))
        active[0].unreadable = ""
        clock.advance(10)
        await repository.refresh()
        assert (
            await ledger.merge_gate(
                "clerk", 1, expected_head_sha=active[0].head, expected_main_sha=MAIN
            )
        ).verdict == "MERGE"
        clock.advance(21)
        expired = await ledger.merge_gate("clerk", 1)
        assert [reason.code for reason in expired.reasons] == ["pr_stale"]


async def test_detail_rotation_survives_restart_and_cooldown(
    tmp_path: Path, repository: Repository
) -> None:
    clock = FixedClock(datetime.fromisoformat(LATER))
    terminal = Pull(101, closed=True, unreadable="text")
    repository.add(
        *(Pull(number) for number in range(1, 21)),
        terminal,
        *(Pull(number, closed=True) for number in range(102, 105)),
    )
    terminal_scope = f"{PULL_DETAIL_ATTEMPT_PREFIX}{terminal.number}"
    async with running_daemon(tmp_path, clock=clock) as (daemon, _, _, _):
        await repository.refresh()
        assert repository.thread_reads == [*range(1, 18), 101, 102]
        attempt = await daemon.ledgers["epic4"].fetch_cursor(REPO, terminal_scope)
        assert attempt is not None
        assert attempt.watermark == ""
        assert attempt.generation == 1

    clock.advance(10)
    repository.pulls[18].unreadable = "rate-limited"
    async with running_daemon(tmp_path, clock=clock) as (daemon, _, _, _):
        ledger = daemon.ledgers["epic4"]
        repository.thread_reads.clear()
        await repository.refresh()
        assert repository.thread_reads == [18]
        rate_limited_scope = f"{PULL_DETAIL_ATTEMPT_PREFIX}18"
        failed = await ledger.fetch_cursor(REPO, rate_limited_scope)
        assert failed is not None
        assert failed.watermark == ""

        repository.thread_reads.clear()
        requests_before = repository.request_count
        await repository.refresh(expect_commit=False)
        assert repository.request_count == requests_before
        assert repository.thread_reads == []
        assert await ledger.fetch_cursor(REPO, rate_limited_scope) == failed
        assert await ledger.fetch_cursor(REPO, terminal_scope) == attempt

        repository.pulls[18].unreadable = ""
        terminal.unreadable = ""
        clock.advance(21)
        await repository.refresh()
        assert repository.thread_reads == [19, 20, *range(1, 16), 103, 104]
        repository.thread_reads.clear()
        clock.advance(10)
        await repository.refresh()
        assert repository.thread_reads[:3] == [16, 17, 18]
        assert repository.thread_reads[17:] == [101]
        completed = await ledger.fetch_cursor(REPO, terminal_scope)
        assert completed is not None
        assert completed.watermark
        assert completed.generation == 2


async def test_head_rate_limit_preserves_healthy_detail_progress_across_resumes(
    tmp_path: Path, repository: Repository
) -> None:
    clock = FixedClock(datetime.fromisoformat(LATER))
    limited = Pull(2, unreadable="rate-limited-checks")
    repository.add(Pull(1), limited, Pull(3), Pull(101, closed=True))

    async with running_daemon(tmp_path, clock=clock) as (daemon, _, _, _):
        ledger = daemon.ledgers["epic4"]
        healthy_scope = f"{PULL_DETAIL_ATTEMPT_PREFIX}3"
        limited_scope = f"{PULL_DETAIL_ATTEMPT_PREFIX}2"
        for expected in ([1], [3, 1]):
            repository.thread_reads.clear()
            await repository.refresh()
            assert repository.thread_reads == expected
            assert repository.last_head_rate_limit == repository.request_count
            clock.advance(21)

        healthy = await ledger.fetch_cursor(REPO, healthy_scope)
        assert healthy is not None
        assert healthy.watermark
        interrupted = await ledger.fetch_cursor(REPO, limited_scope)
        assert interrupted is not None
        assert interrupted.watermark == ""
        assert interrupted.generation == 2
        assert await ledger.fetch_cursor(REPO, f"{PULL_DETAIL_ATTEMPT_PREFIX}101") is None

        limited.unreadable = ""
        repository.thread_reads.clear()
        await repository.refresh()
        assert 101 in repository.thread_reads


async def test_pull_detail_attempt_cannot_complete_a_different_revision(
    tmp_path: Path, repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    pull = Pull(1)
    repository.add(pull)
    async with running_daemon(tmp_path) as (daemon, _, _, _):
        await repository.refresh()
        ledger = daemon.ledgers["epic4"]
        scope = f"{PULL_DETAIL_ATTEMPT_PREFIX}1"
        attempt = await ledger.fetch_cursor(REPO, scope)
        assert attempt is not None
        assert attempt.watermark

        async def move_during_text_read(
            api: GitHubReadClient, client: httpx.AsyncClient, token: str, pinned: PullRequest
        ) -> GitHubResource:
            result = await fetch_pull_text(api, client, token, pinned)
            pull.head_version += 1

            async def advance(uow: UnitOfWork) -> None:
                await uow.save_pull_request(
                    replace(
                        pinned,
                        head_sha=pull.head,
                        version=pinned.version + 1,
                        updated_at=format_time(datetime.fromisoformat(LATER)),
                    )
                )

            await ledger.run("system", advance)
            return result

        with monkeypatch.context() as patches:
            patches.setattr(
                "plotroom.adapters.github.details.fetch_pull_text", move_during_text_read
            )
            await repository.refresh()

        assert await ledger.fetch_cursor(REPO, scope) == attempt
        text_projection = await ledger.github_resource(REPO, PULL_TEXT, "1")
        assert text_projection is not None
        assert text_projection.attributes["head_sha"] != pull.head
        assert (await ledger.merge_gate("clerk", 1)).verdict == "REFUSE"
        await repository.refresh()
        assert (await ledger.merge_gate("clerk", 1)).verdict == "MERGE"
