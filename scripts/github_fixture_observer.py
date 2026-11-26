"""Read-only GitHub provider observer for the dedicated #33 fixture exercise."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import stat
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from github_fixture_acceptance import (
    REPO,
    TOKEN_ENV,
    AcceptanceError,
    Provider,
    checked,
    locked,
    make_config,
    note,
    positive_id,
    require,
    require_pull,
    sha,
    snapshot,
    stdio,
    stop_daemon,
    tool,
)
from mcp import Client

from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.tokens import TokenRegistry
from plotroom.core.events import Event
from plotroom.core.github_projection import (
    CHECKS,
    ISSUES,
    MAIN,
    THREADS,
    GitHubResource,
    checks_for_head,
    main_verdict,
    unresolved_threads,
)
from plotroom.core.triggers import TRIGGER_FIRED

PROJECT = "github-fixture"
BRANCH = re.compile(r"plotroom-33/[a-z0-9][a-z0-9-]{0,31}")
IMAGE = "Fixture image build"
CHECK_NAMES = {"Fixture checks", IMAGE}
UP_NEXT = {
    "arm-thread": "arm-pr",
    "arm-issue": "arm-thread",
    "record": "arm-issue",
    "retire": "record",
}


class ReadOnlyProvider(Provider):
    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        require(method == "GET", "observer provider only permits GET")
        return await super().request(method, path, **kwargs)


def require_private(path: Path, directory: bool) -> None:
    info = path.lstat()
    expected_mode = 0o700 if directory else 0o600
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    require(
        expected_type(info.st_mode)
        and stat.S_IMODE(info.st_mode) == expected_mode
        and info.st_uid == os.getuid(),
        "observer state is not private",
    )


def args_state(base: Any, head: Any, branch: Any) -> dict[str, Any]:
    require(isinstance(branch, str) and BRANCH.fullmatch(branch), "invalid fixture branch")
    base_sha, head_sha = sha(base), sha(head)
    require(base_sha != head_sha, "fixture branch needs a distinct prepared commit")
    return {
        "base": base_sha,
        "head": head_sha,
        "branch": branch,
        "issue_title": f"Plotroom fixture 33 {branch.split('/')[1]}",
    }


async def wait_for[T](
    read: Callable[[], Awaitable[T | None]], message: str, *, seconds: int = 145
) -> T:
    try:
        async with asyncio.timeout(seconds):
            while (result := await read()) is None:  # noqa: ASYNC110 - external projection
                await asyncio.sleep(2)
            return result
    except TimeoutError:
        raise AcceptanceError(message) from None


async def fire_ids(store: SqliteStore, identifier: str) -> list[str]:
    rows = await store.events_after(0, 1000)
    require(len(rows) < 1000, "observer event count exceeded its bound")
    return [
        row.id
        for row in rows
        if row.kind == TRIGGER_FIRED and row.fields["trigger_id"] == identifier
    ]


async def arm(
    client: Client, name: str, predicate: dict[str, Any], *, lane: str | None = None
) -> str:
    options = {"lane": lane} if lane is not None else {}
    row = await checked(
        client,
        "trigger",
        request_id=name,
        predicate={name: predicate},
        action={"notify_orchestrator": {"text": name}},
        **options,
    )
    return row["id"]


async def arm_false(
    client: Client, store: SqliteStore, name: str, predicate: dict[str, Any]
) -> str:
    identifier = await arm(client, name, predicate)
    require(not await fire_ids(store, identifier), "trigger condition was already true")
    return identifier


async def pin_provider(provider: ReadOnlyProvider, state: dict[str, Any]) -> None:
    repo = await provider.get("")
    require(
        repo["full_name"] == REPO
        and repo["default_branch"] == "main"
        and repo["private"] is False
        and repo["fork"] is False,
        "fixture repository identity changed",
    )
    require(await provider.main() == state["base"], "fixture main moved")
    ref = await provider.get(f"/git/ref/heads/{state['branch']}")
    require(
        ref["ref"] == f"refs/heads/{state['branch']}" and ref["object"]["sha"] == state["head"],
        "fixture branch tip changed",
    )


async def verify_main_and_report(
    client: Client, store: SqliteStore, config: Path, tokens: TokenRegistry, state: dict[str, Any]
) -> None:
    async def main_proof() -> tuple[Any, Any] | None:
        main = await store.github_resource(REPO, MAIN)
        if not main or not main.complete or main.attributes.get("head_sha") != state["base"]:
            return None
        verdict = main_verdict(main, require_attribution=True)
        if not verdict or verdict.fields["head_sha"] != state["base"]:
            return None
        images = [row for row in verdict.fields["jobs"] if row["name"] == IMAGE]
        if verdict.fields.get("conclusion") != "success" or not images:
            return None
        image = max(images, key=lambda row: row["id"])
        if image.get("status") != "completed" or image.get("conclusion") != "success":
            return None
        return verdict, image

    verdict, image = await wait_for(main_proof, "same-tip main image verdict was not projected")
    identifier = await arm(client, "main_green_with_images", {"sha": state["base"]})
    fired = await fire_ids(store, identifier)
    require(len(fired) == 1, "same-tip image predicate did not fire at registration")
    main_checks, main_events = await stable_checks(store, state["base"])
    validate_check_events(main_events)
    main_latest = checks_for_head(main_checks, state["base"])
    require(main_latest is not None, "main checks lack complete head attribution")
    for event in main_events:
        note(
            "main-check-transition",
            id=event.fields["id"],
            head=event.fields["head_sha"],
            status=event.fields["status"],
            conclusion=event.fields.get("conclusion"),
            event_id=event.id,
            upstream_id=event.upstream_id,
        )
    require(
        {
            row.fields["name"]
            for row in main_latest or ()
            if row.fields.get("conclusion") == "success"
        }
        >= CHECK_NAMES,
        "main checks are missing a successful fixture job",
    )
    for check in main_latest or ():
        if check.fields["name"] in CHECK_NAMES:
            seen = {
                event.fields["status"]
                for event in main_events
                if event.fields["id"] == check.fields["id"]
            }
            note(
                "main-check-coverage",
                name=check.fields["name"],
                id=check.fields["id"],
                missing=sorted({"queued", "in_progress", "completed"} - seen),
            )
    note(
        "main-images",
        tip=state["base"],
        run=verdict.fields["id"],
        attempt=verdict.fields["attempt"],
        image_job=image["id"],
        firing=fired[0],
    )

    await checked(
        client,
        "lane_start",
        request_id="fixture-run-lane",
        lane="fixture-run",
        worker="owner",
        branch=state["branch"],
        work_items=[],
    )
    await checked(
        client,
        "lane_start",
        request_id="fixture-foreign-lane",
        lane="fixture-foreign",
        worker="foreign",
        branch="local-fixture-foreign",
        work_items=[],
    )
    run_id = f"fixture-33-{os.environ['GITHUB_RUN_ID']}"
    finished = await arm(client, "run_finished", {"run_id": run_id}, lane="fixture-run")
    require(not await fire_ids(store, finished), "run predicate fired before report")
    args = {
        "request_id": "fixture-run-result",
        "lane": "fixture-run",
        "run_id": run_id,
        "state": "succeeded",
    }
    async with stdio(config, tokens.issue(PROJECT, "worker:foreign").token_id) as foreign:
        denied = await tool(foreign, "run_report", **args)
        require(denied.get("error", {}).get("code") == "forbidden", "foreign lane could report")
    async with stdio(config, tokens.issue(PROJECT, "worker:owner").token_id) as owner:
        await checked(owner, "run_report", **args)
    fired = await fire_ids(store, finished)
    require(len(fired) == 1, "owning worker did not produce one run-finished firing")
    note("run-finished", run_id=run_id, foreign_refused=True, firing=fired[0])
    state["run_trigger"] = finished
    state["main_trigger"] = identifier


async def arm_pr(
    provider: ReadOnlyProvider,
    client: Client,
    store: SqliteStore,
    config: Path,
    tokens: TokenRegistry,
    state: dict[str, Any],
    started: datetime,
) -> None:
    require(
        not await provider.get("/pulls", params={"state": "open", "per_page": 100}),
        "fixture has an unexpected open PR",
    )
    require(not await named_issue(provider, state), "fixture issue title is already in use")
    await snapshot(client, 0, started)
    await verify_main_and_report(client, store, config, tokens, state)
    require(
        not await provider.get("/pulls", params={"state": "open", "per_page": 100}),
        "PR appeared before the green trigger was armed",
    )
    history = await provider.get(
        "/pulls", params={"state": "all", "head": f"mgd43b:{state['branch']}", "per_page": 100}
    )
    require(not history, "fixture branch has prior PR history")
    state["green_trigger"] = await arm_false(
        client, store, "pr_green", {"branch_glob": state["branch"]}
    )
    note(
        "pr-armed",
        branch=state["branch"],
        head=state["head"],
        trigger=state["green_trigger"],
        issue_title=state["issue_title"],
    )


async def named_issue(provider: ReadOnlyProvider, state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = await provider.get(
        "/issues", params={"state": "all", "creator": "mgd43b", "per_page": 100}
    )
    return [
        row
        for row in rows
        if "pull_request" not in row
        and row["title"] == state["issue_title"]
        and row["user"]["login"] == "mgd43b"
    ]


async def pr_number(provider: ReadOnlyProvider, state: dict[str, Any]) -> int | None:
    rows = await provider.get("/pulls", params={"state": "open", "per_page": 100})
    if not rows:
        return None
    require(len(rows) == 1 and rows[0]["user"]["login"] == "mgd43b", "unexpected open fixture PR")
    require_pull(rows[0], state["branch"], state["head"], state["base"], "open")
    return positive_id(rows[0]["number"])


async def arm_thread(
    provider: ReadOnlyProvider, client: Client, store: SqliteStore, state: dict[str, Any]
) -> None:
    pr = await wait_for(lambda: pr_number(provider, state), "approved fixture PR was not opened")
    state["pr"] = pr

    async def thread_proof() -> str | None:
        pull = await store.pull_request(REPO, pr)
        threads = await store.github_resource(REPO, THREADS, str(pr))
        if not pull or pull.head_sha != state["head"] or not threads or not threads.complete:
            return None
        if (
            threads.attributes.get("head_sha") != state["head"]
            or threads.attributes.get("pull_version") != pull.version
        ):
            return None
        require(len(threads.records) <= 1, "fixture has more than one review thread")
        if not threads.records:
            return None
        require(len(unresolved_threads(threads)) == 1, "thread resolved before its checkpoint")
        return threads.records[0].key

    state["thread"] = await wait_for(thread_proof, "complete unresolved thread was not projected")
    state["thread_trigger"] = await arm_false(client, store, "pr_threads_resolved", {"number": pr})
    note("thread-armed", pr=pr, trigger=state["thread_trigger"])


async def arm_issue(
    provider: ReadOnlyProvider, client: Client, store: SqliteStore, state: dict[str, Any]
) -> None:
    async def issue_proof() -> int | None:
        rows = await named_issue(provider, state)
        require(len(rows) <= 1, "multiple standalone fixture issues match")
        if not rows:
            return None
        require(rows[0]["state"] == "open", "issue closed before its checkpoint")
        number = positive_id(rows[0]["number"])
        issues = await store.github_resource(REPO, ISSUES)
        if not issues or not issues.complete:
            return None
        if not any(
            row.key == f"issue/{number}" and row.fields["state"] == "open" for row in issues.records
        ):
            return None
        return number

    state["issue"] = await wait_for(issue_proof, "complete open issue was not projected")
    state["issue_trigger"] = await arm_false(
        client, store, "issue_closed", {"number": state["issue"]}
    )
    note("issue-armed", number=state["issue"], trigger=state["issue_trigger"])


def check_state(checks: GitHubResource) -> dict[int, tuple[str, str | None]]:
    return {
        row.fields["id"]: (row.fields["status"], row.fields.get("conclusion"))
        for row in checks.records
    }


def validate_check_events(events: list[Event]) -> None:
    seen: set[str] = set()
    latest: dict[int, tuple[str, str | None]] = {}
    for row in events:
        identifier = row.fields["id"]
        current = (row.fields["status"], row.fields.get("conclusion"))
        require(
            row.upstream_id not in seen and latest.get(identifier) != current,
            "duplicated check facts",
        )
        seen.add(row.upstream_id)
        latest[identifier] = current


async def stable_checks(store: SqliteStore, head: str) -> tuple[GitHubResource, list[Event]]:
    async def read() -> tuple[GitHubResource, list[Event]] | None:
        checks = await store.github_resource(REPO, CHECKS, head)
        if not checks or not checks.complete or not checks.as_of:
            return None
        events = await store.events_after(0, 1000)
        require(len(events) < 1000, "observer event count exceeded its bound")
        if await store.github_resource(REPO, CHECKS, head) != checks:
            return None
        return checks, [
            row
            for row in events
            if row.kind == "github.check_run" and row.fields["head_sha"] == head
        ]

    previous, events = await wait_for(read, "complete checks were not observed")

    async def follow() -> tuple[GitHubResource, list[Event]] | None:
        nonlocal previous, events
        value = await read()
        if value is None or value[0].as_of <= previous.as_of:
            return None
        checks, after = value
        if check_state(previous) != check_state(checks):
            previous, events = checks, after
            return None
        require(events == after, "unchanged check poll added events")
        return checks, after

    return await wait_for(follow, "an unchanged follow-up check poll was not observed")


async def record(
    provider: ReadOnlyProvider, client: Client, store: SqliteStore, state: dict[str, Any]
) -> None:
    pr = state["pr"]
    trigger_keys = (
        "green_trigger",
        "thread_trigger",
        "issue_trigger",
        "run_trigger",
        "main_trigger",
    )

    async def outcomes() -> str | None:
        for key in trigger_keys:
            fired = await fire_ids(store, state[key])
            require(len(fired) <= 1, "a once trigger fired repeatedly")
            if not fired and key != "green_trigger":
                return None
        resource = await store.github_resource(REPO, CHECKS, state["head"])
        return resource.as_of if resource and resource.complete and resource.as_of else None

    await wait_for(outcomes, "one or more approved fixture outcomes were not projected")

    async def finished_green() -> bool | None:
        return True if await fire_ids(store, state["green_trigger"]) else None

    try:
        await wait_for(finished_green, "green trigger did not fire", seconds=75)
    except AcceptanceError:
        note("green-trigger-pending", pr=pr)
    checks, check_events = await stable_checks(store, state["head"])
    await wait_for(outcomes, "once-trigger evidence changed on the follow-up poll")
    require(await pr_number(provider, state) == pr, "fixture PR retired before gate readback")
    registrations = (await checked(client, "triggers"))["triggers"]
    binding = [row["bindings"] for row in registrations if row["id"] == state["green_trigger"]]
    require(binding == [{"$": pr}], "green trigger did not bind the approved PR")
    gate = await checked(client, "merge_gate", pr=pr)
    require(gate["head_sha"] == state["head"] and gate["as_of"], "gate lacks exact-head evidence")
    reasons = [row["code"] for row in gate["reasons"]]
    evidence_gap = {
        "pr_unavailable",
        "pr_incomplete",
        "pr_stale",
        "checks_unreadable",
        "checks_stale",
        "review_threads_incomplete",
    }
    note(
        "gate",
        pr=pr,
        head=gate["head_sha"],
        as_of=gate["as_of"],
        verdict=gate["verdict"],
        reasons=reasons,
        evidence_pending=bool(evidence_gap.intersection(reasons)),
    )

    events = await store.events_after(0, 1000)
    require(len(events) < 1000, "observer event count exceeded its bound")
    validate_check_events(check_events)
    for row in check_events:
        note(
            "check-transition",
            id=row.fields["id"],
            head=row.fields["head_sha"],
            status=row.fields["status"],
            conclusion=row.fields.get("conclusion"),
            event_id=row.id,
            upstream_id=row.upstream_id,
        )
    latest = checks_for_head(checks, state["head"])
    require(latest is not None, "latest checks lack complete head attribution")
    expected = {
        row.fields["name"]: row.fields["id"]
        for row in latest or ()
        if row.fields["name"] in CHECK_NAMES
        and row.fields["status"] == "completed"
        and row.fields.get("conclusion") == "success"
    }
    require(expected.keys() == CHECK_NAMES, "named fixture checks are not green")
    for name, identifier in expected.items():
        seen = {row.fields["status"] for row in check_events if row.fields["id"] == identifier}
        note(
            "check-coverage",
            name=name,
            id=identifier,
            missing=sorted({"queued", "in_progress", "completed"} - seen),
        )
    thread_events = [
        row.fields["resolved"]
        for row in events
        if row.kind == "github.review_thread"
        and row.fields["number"] == pr
        and row.fields["id"] == state["thread"]
    ]
    require(thread_events == [False, True], "thread open/resolved evidence is missing or repeated")
    threads = await store.github_resource(REPO, THREADS, str(pr))
    require(
        threads and threads.complete and not unresolved_threads(threads),
        "fixture thread remains unresolved",
    )
    issue_events = Counter(
        row.kind
        for row in events
        if row.kind in ("github.issue_opened", "github.issue_closed")
        and row.fields.get("number") == state["issue"]
    )
    require(
        issue_events == {"github.issue_opened": 1, "github.issue_closed": 1},
        "issue open/closed evidence is missing or repeated",
    )
    note(
        "outcomes",
        pr=pr,
        issue=state["issue"],
        thread=state["thread"],
        firings={key: await fire_ids(store, state[key]) for key in trigger_keys},
    )


async def retire(
    provider: ReadOnlyProvider, client: Client, state: dict[str, Any], started: datetime
) -> None:
    async def closed() -> bool | None:
        row = await provider.get(f"/pulls/{state['pr']}")
        require(not row.get("merged"), "fixture PR was unexpectedly merged")
        if row["state"] == "open":
            return None
        require_pull(row, state["branch"], state["head"], state["base"], "closed")
        return True

    await wait_for(closed, "fixture PR was not retired")
    await snapshot(client, 0, started)
    await pin_provider(provider, state)
    note("retired", pr=state["pr"], head=state["head"], unchanged_main=state["base"])


async def execute(root: Path, stage: str, state: dict[str, Any]) -> None:
    token_path = root / "github-token"
    require_private(token_path, False)
    require(TOKEN_ENV not in os.environ, "GitHub credential environment override is set")
    token = (await asyncio.to_thread(token_path.read_text)).strip()
    require(re.fullmatch(r"github_pat_[A-Za-z0-9_]+", token), "scoped fine-grained token required")
    home = root / "home"
    if stage == "arm-pr":
        home.mkdir(mode=0o700)
    require_private(home, True)
    os.environ["HOME"] = str(home)
    config = root / "fixture.toml"
    data = root / "data"
    tokens = TokenRegistry(data)
    if stage == "arm-pr":
        make_config(root, poll_seconds=4)
        state["token_id"] = tokens.issue(PROJECT, "orchestrator").token_id
    else:
        require(locked(data), "observer daemon did not survive the previous checkpoint")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    store = SqliteStore(PROJECT, data)
    started = datetime.now(UTC)
    try:
        async with (
            asyncio.timeout(310),
            httpx.AsyncClient(
                headers=headers, follow_redirects=False, trust_env=False, timeout=12
            ) as http,
            stdio(config, state["token_id"]) as client,
        ):
            provider = ReadOnlyProvider(http, acceptance=False)
            await pin_provider(provider, state)
            if stage == "arm-pr":
                await arm_pr(provider, client, store, config, tokens, state, started)
            elif stage == "arm-thread":
                await arm_thread(provider, client, store, state)
            elif stage == "arm-issue":
                await arm_issue(provider, client, store, state)
            elif stage == "record":
                await record(provider, client, store, state)
            else:
                await retire(provider, client, state, started)
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("arm-pr", *UP_NEXT, "cleanup"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--branch")
    args = parser.parse_args()
    root: Path = args.directory
    os.umask(0o077)
    try:
        if args.stage == "cleanup" and not root.exists():
            return 0
        require_private(root, True)
        if args.stage == "cleanup":
            asyncio.run(stop_daemon(root / "data"))
            return 0
        state_path = root / "observer.json"
        if args.stage == "arm-pr":
            require(not state_path.exists(), "observer is already initialized")
            state = args_state(args.base, args.head, args.branch)
        else:
            require_private(state_path, False)
            state = json.loads(state_path.read_text())
            require(state["stage"] == UP_NEXT[args.stage], "observer checkpoint is out of order")
        asyncio.run(execute(root, args.stage, state))
        state["stage"] = args.stage
        state_path.write_text(json.dumps(state), encoding="utf-8")
    except AcceptanceError as exc:
        note("observer-stopped", stage=args.stage, reason=str(exc))
        return 1
    except Exception as exc:
        note("observer-stopped", stage=args.stage, reason=type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
