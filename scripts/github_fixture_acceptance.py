"""Operator-dispatched acceptance against the dedicated Plotroom GitHub fixture."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import signal
import stat
import sys
import tomllib
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from plotroom.adapters.github import github_secret_name, is_rate_limited
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.work_items_bmad import BmadTicketTree
from plotroom.app.lock import LOCK_FILE_NAME, DataDirLock, LockError
from plotroom.app.tokens import TokenRegistry
from plotroom.core.github import PULL_REQUESTS
from plotroom.core.trigger_actions import NOTIFICATION_ITEM
from plotroom.core.triggers import TRIGGER_FIRED

REPO = "mgd43b/plotroom-test"
PROJECT = "github-fixture"
WORKFLOW = "plotroom-fixture-ci.yml"
JOBS = {"Fixture checks", "Fixture image build"}
SOURCE = Path(__file__).resolve().parents[1]
TREE = "initiative-demo/tickets/epic-inbox/tickets"
STORY = f"{TREE}/tickets.toml"
TREE_FILES = (
    "_bmad/custom/ticketing-store-config.toml",
    "initiative-demo/initiative-demo.md",
    "initiative-demo/tickets/tickets.toml",
    "initiative-demo/tickets/epic-inbox/epic-inbox.md",
    STORY,
)
API = "https://api.github.com"
TOKEN_ENV = github_secret_name(PROJECT)


class AcceptanceError(Exception):
    """A failure whose message can be published in a run summary."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def note(event: str, **fields: object) -> None:
    print(json.dumps({"fixture": event, **fields}), flush=True)


def sha(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise AcceptanceError("invalid commit SHA in fixture evidence")
    return value


def positive_id(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise AcceptanceError("invalid provider ID in fixture evidence")
    return value


class Provider:
    def __init__(self, client: httpx.AsyncClient, *, acceptance: bool) -> None:
        self.client = client
        self.acceptance = acceptance
        self.preflight_passed = False
        self.denied: dict[str, int] = {}
        self.partial: set[str] = set()

    async def request(
        self, method: str, path: str, *, optional: bool = False, **kwargs: Any
    ) -> Any:
        require(
            (not path and method == "GET")
            or (path.startswith("/") and not path.startswith("//") and ".." not in path),
            "invalid fixture resource path",
        )
        if method != "GET":
            require(self.acceptance and self.preflight_passed, "fixture mutation before preflight")
            allowed = (
                (method == "POST" and path in {"/git/refs", "/pulls"})
                or (method == "PUT" and path in {"/contents/README.md", f"/contents/{STORY}"})
                or (method == "PATCH" and re.fullmatch(r"/pulls/[1-9][0-9]*", path))
                or (method == "PUT" and re.fullmatch(r"/pulls/[1-9][0-9]*/merge", path))
                or (
                    method == "DELETE"
                    and re.fullmatch(r"/git/refs/heads/plotroom-fixture/[1-9][0-9]*", path)
                )
            )
            require(allowed, "unapproved fixture mutation")
        response = await self.client.request(
            method, f"{API}/repos/{REPO}{path}", follow_redirects=False, **kwargs
        )
        if is_rate_limited(response):
            raise AcceptanceError("GitHub rate limit reached; fixture stopped")
        if optional and response.status_code in (401, 403, 404):
            self.denied[path] = response.status_code
            return None
        if not response.is_success:
            raise AcceptanceError(
                f"fixture provider {method} failed with HTTP {response.status_code}"
            )
        if method == "GET":
            self.partial.discard(path)
            if "next" in response.links:
                if not optional:
                    raise AcceptanceError("fixture provider evidence exceeded one page")
                self.partial.add(path)
        return None if response.status_code == 204 else response.json()

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def content(self, path: str, revision: str) -> tuple[bytes, str]:
        require(path in (*TREE_FILES, "README.md"), "unexpected fixture file")
        row = await self.get(f"/contents/{path}", params={"ref": sha(revision)})
        require(row["encoding"] == "base64" and row["type"] == "file", "fixture blob unavailable")
        data = base64.b64decode("".join(row["content"].split()), validate=True)
        require(len(data) <= 64 * 1024, "fixture blob exceeded size limit")
        return data, sha(row["sha"])

    async def main(self) -> str:
        return sha((await self.get("/branches/main"))["commit"]["sha"])

    async def ready_ci(self, revision: str, branch: str, event: str) -> int:
        async with asyncio.timeout(150):
            while True:
                page = await self.get(
                    f"/actions/workflows/{WORKFLOW}/runs",
                    params={
                        "head_sha": sha(revision),
                        "branch": branch,
                        "event": event,
                        "per_page": 100,
                    },
                )
                matches = [
                    row
                    for row in page["workflow_runs"]
                    if row["head_sha"] == revision
                    and row["head_branch"] == branch
                    and row["event"] == event
                    and row["head_repository"]["full_name"] == REPO
                ]
                if matches and matches[0]["status"] == "completed":
                    run = matches[0]
                    require(run["conclusion"] == "success", "fixture CI completed without success")
                    run_id = positive_id(run["id"])
                    attempt = positive_id(run["run_attempt"])
                    require(attempt == 1, "fixture CI first attempt is required for acceptance")
                    jobs = await self.get(f"/actions/runs/{run_id}/attempts/{attempt}/jobs")
                    require(
                        {job["name"] for job in jobs["jobs"] if job["conclusion"] == "success"}
                        >= JOBS,
                        "fixture CI is missing a successful expected job",
                    )
                    return run_id
                await asyncio.sleep(5)


async def preflight(provider: Provider) -> tuple[str, int, dict[str, Any] | None]:
    repo = await provider.get("")
    require(
        repo["full_name"] == REPO
        and repo["default_branch"] == "main"
        and repo["private"] is False
        and repo["fork"] is False,
        "fixture repository identity or scope changed",
    )
    tip = await provider.main()
    pulls = await provider.get("/pulls", params={"state": "open", "per_page": 100})
    require(isinstance(pulls, list), "fixture PR list incomplete")
    for path in TREE_FILES:
        await provider.content(path, tip)
    note("core-reads", auth="scoped-pat", tip=tip, open_prs=len(pulls), tree_files=len(TREE_FILES))
    endpoints = {
        "checks": (f"/commits/{tip}/check-runs", "check_runs"),
        "actions": ("/actions/runs", "workflow_runs"),
        "issues": ("/issues", None),
        "deployments": ("/deployments", None),
    }
    results: dict[str, Any] = {}
    for name, (endpoint, key) in endpoints.items():
        page = await provider.get(endpoint, optional=True, params={"per_page": 100})
        rows = None if page is None else (page[key] if key else page)
        require(rows is None or isinstance(rows, list), "fixture resource response is malformed")
        rows = cast("list[dict[str, Any]] | None", rows)
        if name == "checks" and rows is not None:
            require(all(row["head_sha"] == tip for row in rows), "check head attribution mismatch")
        results[name] = rows
        note(
            name,
            http_status=provider.denied.get(endpoint, 200),
            count=len(rows) if rows is not None else None,
            available=rows is not None,
            complete=rows is not None and endpoint not in provider.partial,
        )

    sample = None
    deployments = results["deployments"]
    if deployments:
        deployment = deployments[0]
        number = positive_id(deployment["id"])
        path = f"/deployments/{number}/statuses"
        statuses = await provider.get(path, optional=True)
        if statuses:
            sample = {
                "deployment_id": number,
                "status_id": positive_id(statuses[0]["id"]),
                "sha": sha(deployment["sha"]),
                "environment": statuses[0].get("environment") or deployment["environment"],
                "state": statuses[0]["state"],
            }
        else:
            note(
                "deployment-status-sample-missing",
                http_status=provider.denied.get(path, 200),
                complete=statuses is not None and path not in provider.partial,
            )
    return tip, len(pulls), sample


async def eventually[T](
    read: Callable[[], Awaitable[T]], ready: Callable[[T], bool], message: str
) -> T:
    try:
        async with asyncio.timeout(60):
            while True:
                value = await read()
                if ready(value):
                    return value
                await asyncio.sleep(1)
    except TimeoutError:
        raise AcceptanceError(message) from None


def locked(data: Path) -> bool:
    if not (data / LOCK_FILE_NAME).exists():
        return False
    lock = DataDirLock(data)
    try:
        lock.acquire()
    except LockError:
        return True
    lock.release()
    return False


async def stop_daemon(data: Path) -> None:
    if locked(data):
        pid = positive_id(int((data / LOCK_FILE_NAME).read_text()))
        require(pid > 1, "invalid isolated daemon PID")
        os.kill(pid, signal.SIGTERM)
        async with asyncio.timeout(15):
            while locked(data):  # noqa: ASYNC110 - external process lock
                await asyncio.sleep(0.2)


def make_config(root: Path) -> Path:
    config = root / "fixture.toml"
    config.write_text(
        f"""
data_dir = {json.dumps(str(root / "data"))}
port = 0
tick_seconds = 2

[projects.{PROJECT}]
github_ticket_tree = "{TREE}"
bmad_installation = {json.dumps(str(SOURCE))}
[projects.{PROJECT}.workers.owner]
[projects.{PROJECT}.workers.foreign]
[projects.{PROJECT}.github]
repository = "{REPO}"
token_file = "github-token"
poll_seconds = 8
ci_workflow = "{WORKFLOW}"
image_jobs = ["Fixture image build"]
min_checks = 2
controller_merges = false
""",
        encoding="utf-8",
    )
    return config


@asynccontextmanager
async def stdio(config: Path, token_id: str) -> AsyncGenerator[Client]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLOTROOM_") and key not in {"GH_TOKEN", "GITHUB_TOKEN"}
    }
    params = StdioServerParameters(
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
        env=env,
    )
    with open(os.devnull, "w", encoding="utf-8") as quiet:  # noqa: ASYNC230
        async with Client(
            stdio_client(params, errlog=quiet), mode="legacy", read_timeout_seconds=20
        ) as client:
            yield client


async def tool(client: Client, name: str, **args: Any) -> dict[str, Any]:
    result = await client.call_tool(name, args)
    require(len(result.content) == 1, "unexpected stdio result shape")
    result_text = result.content[0]
    if not isinstance(result_text, TextContent):
        raise AcceptanceError("unexpected stdio result format")
    decoded: dict[str, Any] = json.loads(result_text.text)
    return decoded


async def checked(client: Client, name: str, **args: Any) -> dict[str, Any]:
    result = await tool(client, name, **args)
    require(result.get("ok") is True, f"Plotroom tool {name} failed")
    return result["data"]


async def snapshot(client: Client, count: int, after: datetime) -> dict[str, Any]:
    return await eventually(
        lambda: checked(client, "snapshot"),
        lambda row: (
            row.get("project") == PROJECT
            and row.get("open_prs") == count
            and (github := row.get("github", {})).get("available") is True
            and isinstance(github.get("as_of"), str)
            and datetime.fromisoformat(github["as_of"]) >= after
        ),
        "fresh file-only stdio snapshot was not observed",
    )


async def mirror(
    provider: Provider, store: SqliteStore, root: Path, revision: str
) -> tuple[str, ...]:
    checkout = root / revision
    for path in TREE_FILES:
        data, _ = await provider.content(path, revision)
        target = checkout / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    expected = await BmadTicketTree(checkout / TREE, SOURCE, project_root=checkout).ready_items()
    require(bool(expected), "trusted local ticket reader found no ready work")
    await eventually(
        store.ready_work_items, lambda actual: actual == expected, "BMad mirror mismatch"
    )
    return tuple(item.ref for item in expected)


async def branch_file(provider: Provider, branch: str, path: str, old: str, data: bytes) -> str:
    require(path in ("README.md", STORY), "unapproved fixture branch file")
    head = await provider.request(
        "PUT",
        f"/contents/{path}",
        json={
            "branch": branch,
            "sha": old,
            "message": "Exercise the Plotroom fixture",
            "content": base64.b64encode(data).decode("ascii"),
        },
    )
    return sha(head["commit"]["sha"])


async def observe_pull(
    store: SqliteStore, client: Client, number: int, state: str, head: str, count: int
) -> None:
    await eventually(
        lambda: store.pull_request(REPO, number),
        lambda row: (
            row is not None and row.complete and row.state == state and row.head_sha == head
        ),
        f"PR {number} did not project state {state} at the expected head",
    )
    await snapshot(client, count, datetime.min.replace(tzinfo=UTC))


async def trigger(client: Client, number: int, label: str) -> str:
    result = await checked(
        client,
        "trigger",
        request_id=label,
        predicate={"pr_merged": {"number": number}},
        action={"notify_orchestrator": {"text": label}},
    )
    return result["id"]


def require_pull(row: dict[str, Any], branch: str, head: str, base: str, state: str) -> None:
    require(
        row["state"] == state
        and not row.get("merged")
        and row["head"]["sha"] == head
        and row["head"]["ref"] == branch
        and row["head"]["repo"]["full_name"] == REPO
        and row["base"]["ref"] == "main"
        and row["base"]["repo"]["full_name"] == REPO
        and row["base"]["sha"] == base,
        "fixture pull request identity, head, state, or base changed",
    )


class OwnedFixture:
    def __init__(self, branch: str, tip: str) -> None:
        self.branch = branch
        self.head = tip
        self.tip = tip
        self.number: int | None = None
        self.merged = False

    async def cleanup(self, provider: Provider) -> None:
        if self.merged:
            return
        ref = f"refs/heads/{self.branch}"
        actual = await provider.get(f"/git/ref/heads/{self.branch}")
        require(
            actual["ref"] == ref and actual["object"]["sha"] == self.head,
            "disposable branch head changed",
        )
        candidates = await provider.get(
            "/pulls", params={"state": "all", "head": f"mgd43b:{self.branch}", "per_page": 100}
        )
        expected = [] if self.number is None else [self.number]
        require([row["number"] for row in candidates] == expected, "fixture PR ownership ambiguous")
        if self.number is not None:
            path = f"/pulls/{self.number}"
            pull = await provider.get(path)
            state = pull["state"]
            require(state in ("open", "closed"), "unexpected disposable PR state")
            require_pull(pull, self.branch, self.head, self.tip, state)
            if state == "open":
                closed = await provider.request("PATCH", path, json={"state": "closed"})
                require_pull(closed, self.branch, self.head, self.tip, "closed")
        latest = await provider.get(f"/git/ref/heads/{self.branch}")
        require(
            latest["ref"] == ref and latest["object"]["sha"] == self.head,
            "disposable branch changed during cleanup",
        )
        await provider.request("DELETE", f"/git/refs/heads/{self.branch}")
        note("run-owned-fixture-cleaned", branch=self.branch, number=self.number)


@asynccontextmanager
async def owned_fixture(provider: Provider, run_id: str, tip: str) -> AsyncGenerator[OwnedFixture]:
    branch = f"plotroom-fixture/{run_id}"
    ref = f"refs/heads/{branch}"
    created = await provider.request("POST", "/git/refs", json={"ref": ref, "sha": tip})
    require(created["ref"] == ref and created["object"]["sha"] == tip, "branch creation unverified")
    owned = OwnedFixture(branch, tip)
    try:
        yield owned
    except Exception, asyncio.CancelledError:
        try:
            async with asyncio.timeout(30):
                await owned.cleanup(provider)
        except (Exception, asyncio.CancelledError) as exc:
            note(
                "fixture-cleanup-pending",
                branch=branch,
                number=owned.number,
                reason=str(exc) if isinstance(exc, AcceptanceError) else type(exc).__name__,
            )
        raise


async def lifecycle(
    provider: Provider,
    store: SqliteStore,
    config: Path,
    token_id: str,
    root: Path,
    tip: str,
    count: int,
    run_id: str,
) -> None:
    require(await provider.main() == tip, "fixture main moved after preflight")
    run = await provider.ready_ci(tip, "main", "push")
    require(await provider.main() == tip, "fixture main moved while reading CI")
    provider.preflight_passed = True
    async with owned_fixture(provider, run_id, tip) as owned:
        branch = owned.branch
        note("fixture-branch", branch=branch, main_ci=run)
        base_readme, old = await provider.content("README.md", tip)
        first = await branch_file(
            provider, branch, "README.md", old, base_readme + b"\nProjection trial.\n"
        )
        owned.head = first
        opened = await provider.request(
            "POST",
            "/pulls",
            json={"title": "Verify Plotroom projection", "head": branch, "base": "main"},
        )
        number = positive_id(opened["number"])
        require_pull(opened, branch, first, tip, "open")
        owned.number = number
        note("fixture-pr", number=number, head=first)
        async with stdio(config, token_id) as client:
            await observe_pull(store, client, number, "open", first, count + 1)
            early = await trigger(client, number, "Fixture merge observation")
            story, old = await provider.content(STORY, first)
            entries = tomllib.loads(story.decode("utf-8"))["entry"]
            entry_id = max(positive_id(entry["id"]) for entry in entries) + 1
            addition = (
                f'\n[[entry]]\nid = {entry_id}\ntype = "story"\ntitle = "Fixture follow-up"\n'
            )
            second = await branch_file(
                provider,
                branch,
                STORY,
                old,
                story + addition.encode(),
            )
            owned.head = second
            await observe_pull(store, client, number, "open", second, count + 1)
            current = await provider.get(f"/pulls/{number}")
            require_pull(current, branch, second, tip, "open")
            await provider.request("PATCH", f"/pulls/{number}", json={"state": "closed"})
            await observe_pull(store, client, number, "closed", second, count)
            before = await store.fetch_cursor(REPO, PULL_REQUESTS)
            if before is None:
                raise AcceptanceError("missing pre-restart PR cursor")

        await stop_daemon(root / "data")
        checkpoint = await store.fetch_cursor(REPO, PULL_REQUESTS)
        if (
            checkpoint is None
            or checkpoint.generation < before.generation
            or checkpoint.watermark < before.watermark
        ):
            raise AcceptanceError("PR cursor lost at restart")
        restarted = datetime.now(UTC)
        async with stdio(config, token_id) as client:
            await snapshot(client, count, restarted)
            after = await store.fetch_cursor(REPO, PULL_REQUESTS)
            require(
                after is not None
                and after.generation > checkpoint.generation
                and after.watermark >= checkpoint.watermark,
                "restarted daemon did not advance the persisted PR cursor",
            )
            current = await provider.get(f"/pulls/{number}")
            require_pull(current, branch, second, tip, "closed")
            await provider.request("PATCH", f"/pulls/{number}", json={"state": "open"})
            await observe_pull(store, client, number, "open", second, count + 1)

            pr_run = await provider.ready_ci(second, branch, "pull_request")
            current = await provider.get(f"/pulls/{number}")
            require_pull(current, branch, second, tip, "open")
            require(
                current["mergeable"] is True and await provider.main() == tip,
                "fixture PR or protected base changed before merge",
            )
            merged = await provider.request(
                "PUT", f"/pulls/{number}/merge", json={"sha": second, "merge_method": "merge"}
            )
            require(merged.get("merged") is True, "fixture merge was refused")
            owned.merged = True
            main = sha(merged["sha"])
            await observe_pull(store, client, number, "merged", second, count)
            require(await provider.main() == main, "main tip does not match fixture merge")
            late = await trigger(client, number, "Fixture already merged")
            committed = await store.events_after(0, 1000)
            require(
                sum(
                    event.kind == TRIGGER_FIRED and event.upstream_id == late for event in committed
                )
                == 1,
                "already-merged registration did not fire on creation",
            )
            refs = await mirror(provider, store, root, main)
            require(f"1.{entry_id}" in refs, "new ready ticket is missing after merge")
            await verify_events(store, number, first, second, early, late)
            note(
                "core-pr-lifecycle-verified",
                number=number,
                head=second,
                main=main,
                pr_ci=pr_run,
                ready_refs=refs,
            )


async def verify_events(
    store: SqliteStore, number: int, first: str, second: str, early: str, late: str
) -> None:
    expected = Counter(
        {
            ("github.pr_opened", first): 1,
            ("github.pr_head_changed", second): 1,
            ("github.pr_closed", second): 1,
            ("github.pr_reopened", second): 1,
            ("github.pr_merged", second): 1,
        }
    )

    async def evidence() -> tuple[Counter[tuple[str, str]], Counter[str], Counter[str]]:
        events = await store.events_after(0, 1000)
        _, items = await store.read_next("orchestrator", 0, 100)
        require(len(events) < 1000, "fixture event evidence exceeded its bound")
        return (
            Counter(
                (e.kind, e.fields["head_sha"])
                for e in events
                if e.kind.startswith("github.pr_") and e.fields.get("number") == number
            ),
            Counter(
                e.upstream_id
                for e in events
                if e.kind == TRIGGER_FIRED and e.upstream_id in (early, late)
            ),
            Counter(
                item.context["text"]
                for item in items
                if item.kind == NOTIFICATION_ITEM.name
                and item.context.get("text")
                in {"Fixture merge observation", "Fixture already merged"}
            ),
        )

    await eventually(
        evidence,
        lambda row: (
            row
            == (
                expected,
                Counter((early, late)),
                Counter(("Fixture merge observation", "Fixture already merged")),
            )
        ),
        "PR events or trigger notifications were missing or duplicated",
    )


async def deployment(
    client: Client,
    config: Path,
    store: SqliteStore,
    tokens: TokenRegistry,
    tip: str,
    sample: dict[str, Any] | None,
) -> None:
    await checked(
        client,
        "lane_start",
        request_id="deploy-lane",
        lane="fixture-report",
        worker="owner",
        branch="fixture-report",
        work_items=[],
    )
    args = {
        "request_id": "deploy-report",
        "lane": "fixture-report",
        "environment": "Fixture report",
        "sha": tip,
        "state": "success",
    }
    async with stdio(config, tokens.issue(PROJECT, "worker:foreign").token_id) as foreign:
        denied = await tool(foreign, "deploy_report", **args)
        require(
            denied.get("error", {}).get("code") == "forbidden", "wrong lane could report deployment"
        )
    async with stdio(config, tokens.issue(PROJECT, "worker:owner").token_id) as owner:
        first = await checked(owner, "deploy_report", **args)
        replay = await checked(owner, "deploy_report", **{**args, "request_id": "deploy-repeat"})
        require(
            first["created"] is True and replay == {"event": first["event"], "created": False},
            "fixture lane report did not deduplicate",
        )
    note("lane-report", foreign_refused=True, exact_once=True)
    if sample is not None:
        await eventually(
            lambda: store.events_after(0, 1000),
            lambda events: (
                sum(
                    event.kind == "deploy.status"
                    and all(event.fields.get(k) == v for k, v in sample.items())
                    for event in events
                )
                == 1
            ),
            "recorded deployment status did not project once",
        )
        note(
            "deployment-sample",
            deployment_id=sample["deployment_id"],
            status_id=sample["status_id"],
        )
    else:
        note("deployment-sample-pending")


async def execute(root: Path, provider: Provider, run_id: str) -> None:
    started = datetime.now(UTC)
    tip, count, sample = await preflight(provider)
    config = make_config(root)
    data = root / "data"
    require(not data.exists(), "fixture state directory is already in use")
    tokens = TokenRegistry(data)
    token_id = tokens.issue(PROJECT, "orchestrator").token_id
    store = SqliteStore(PROJECT, data)
    try:
        async with stdio(config, token_id) as client:
            board = await snapshot(client, count, started)
            require(locked(data), "stdio did not start the isolated daemon")
            refs = await mirror(provider, store, root, tip)
            note(
                "preflight-passed",
                phase="acceptance" if provider.acceptance else "preflight",
                as_of=board["github"]["as_of"],
                tip=tip,
                ready_refs=refs,
            )
            if not provider.acceptance:
                return
            await deployment(client, config, store, tokens, tip, sample)
        await lifecycle(provider, store, config, token_id, root, tip, count, run_id)
    finally:
        try:
            await stop_daemon(data)
        finally:
            await store.close()


async def run(root: Path, phase: str) -> None:
    info = await asyncio.to_thread(root.lstat)
    require(
        stat.S_ISDIR(info.st_mode)
        and stat.S_IMODE(info.st_mode) == 0o700
        and info.st_uid == os.getuid(),
        "fixture directory is not private",
    )
    private_home = root / "home"
    private_home.mkdir(mode=0o700)
    os.environ["HOME"] = str(private_home)
    token_path = root / "github-token"
    info = await asyncio.to_thread(token_path.lstat)
    require(
        stat.S_ISREG(info.st_mode)
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_uid == os.getuid()
        and info.st_size <= 1024,
        "fixture credential file is not private",
    )
    require(TOKEN_ENV not in os.environ, "fixture daemon GitHub environment override is set")
    token = (await asyncio.to_thread(token_path.read_text)).strip()
    require(
        re.fullmatch(r"github_pat_[A-Za-z0-9_]+", token), "a scoped fine-grained PAT is required"
    )
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    require(re.fullmatch(r"[1-9][0-9]{0,19}", run_id), "invalid workflow run ID")
    require(
        phase != "acceptance" or os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
        "dispatch a newly approved run for acceptance",
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    async with (
        asyncio.timeout(580),
        httpx.AsyncClient(
            headers=headers, follow_redirects=False, trust_env=False, timeout=12
        ) as client,
    ):
        await execute(root, Provider(client, acceptance=phase == "acceptance"), run_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("preflight", "acceptance"), default="preflight")
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.directory, args.phase))
    except AcceptanceError as exc:
        note("stopped", reason=str(exc))
        return 1
    except Exception as exc:
        note("stopped", reason=type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
