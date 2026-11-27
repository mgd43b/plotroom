"""One operator-authorized branch-update observation in the dedicated fixture."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
from github_fixture_acceptance import (
    API,
    PROJECT,
    REPO,
    TOKEN_ENV,
    AcceptanceError,
    Provider,
    checked,
    eventually,
    make_config,
    note,
    positive_id,
    require,
    require_pull,
    sha,
    stdio,
    stop_daemon,
)
from github_fixture_observer import require_private

from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app.tokens import TokenRegistry
from plotroom.core.github import PR_HEAD_CHANGED

ANCESTOR = "86ecdf31c557eaa1bf619bfa3fca29d213d7c6ef"
BASE = "9a6ce8a89a15046c18dbfa5d7d2af88bb0edbc14"
REPO_ID = 1473184448
SOURCE_REPO = "mgd43b/plotroom"
WORKFLOW_PATH = ".github/workflows/github-fixture-branch-update.yml"
OLD_HEADING = b"# Plotroom test fixture\n"
NEW_HEADING = b"# Plotroom branch-update fixture\n"


@dataclass(frozen=True)
class Scope:
    source: str
    ancestor: str
    base: str
    run_id: str

    @property
    def branch(self) -> str:
        return f"plotroom-67/{self.run_id}"

    @property
    def ref(self) -> str:
        return f"refs/heads/{self.branch}"

    @property
    def ref_path(self) -> str:
        return f"/git/ref/heads/{self.branch}"


def scope_for(args: argparse.Namespace, env: Mapping[str, str]) -> Scope:
    source, ancestor, base = sha(args.source), sha(args.ancestor), sha(args.base)
    run_id = env.get("GITHUB_RUN_ID", "")
    require(
        env.get("GITHUB_SERVER_URL") == "https://github.com"
        and env.get("GITHUB_REPOSITORY") == SOURCE_REPO
        and env.get("GITHUB_REF") == "refs/heads/main"
        and env.get("GITHUB_SHA") == source
        and env.get("GITHUB_WORKFLOW_REF") == f"{SOURCE_REPO}/{WORKFLOW_PATH}@refs/heads/main"
        and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and env.get("GITHUB_ACTOR") == env.get("GITHUB_TRIGGERING_ACTOR") == "mgd43b"
        and env.get("GITHUB_RUN_ATTEMPT") == "1"
        and re.fullmatch(r"[1-9][0-9]*", run_id),
        "fixture requires the owner and first dispatch attempt on reviewed main",
    )
    require((ancestor, base) == (ANCESTOR, BASE), "fixture pins differ from the reviewed pair")
    if args.phase != "preflight":
        require(env.get("FIXTURE_WRITES_AUTHORIZED") == "true", "fixture writes lack authorization")
    return Scope(source, ancestor, base, run_id)


def record(path: Path, data: dict[str, Any]) -> None:
    # Exclusive creation consumes the attempt even if the process exits during submission.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_record(path: Path) -> dict[str, Any]:
    require_private(path, False)
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "invalid fixture journal")
    return value


class Fixture(Provider):
    def __init__(self, client: httpx.AsyncClient, scope: Scope, phase: str) -> None:
        # The broader acceptance provider always stays read-only.
        super().__init__(client, acceptance=False)
        self.scope = scope
        self.phase = phase
        self.number: int | None = None

    async def _write(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        body = json or {}
        operation = (method, path)
        branch = self.scope.branch
        submit = (
            (
                operation == ("POST", "/git/refs")
                and body == {"ref": self.scope.ref, "sha": self.scope.ancestor}
            )
            or (operation == ("PUT", "/contents/README.md") and body.get("branch") == branch)
            or (
                operation == ("POST", "/pulls")
                and body.get("head") == branch
                and body.get("base") == "main"
            )
            or (
                self.number is not None
                and operation == ("PUT", f"/pulls/{self.number}/update-branch")
                and body.keys() == {"expected_head_sha"}
                and sha(body["expected_head_sha"])
            )
        )
        cleanup = (operation == ("DELETE", f"/git/refs/heads/{branch}") and not body) or (
            self.number is not None
            and operation == ("PATCH", f"/pulls/{self.number}")
            and body == {"state": "closed"}
        )
        require(
            (self.phase == "submit" and submit) or (self.phase == "observe" and cleanup),
            "fixture write is outside the allowed phase or path",
        )
        return await self.client.request(
            method, f"{API}/repos/{REPO}{path}", follow_redirects=False, json=json
        )

    async def check_repository(self) -> None:
        row = await self.get("")
        require(
            row["id"] == REPO_ID
            and row["full_name"] == REPO
            and row["default_branch"] == "main"
            and row["private"] is False
            and row["fork"] is False,
            "fixture repository identity changed",
        )
        await self.check_main()

    async def check_main(self) -> None:
        require(await self.main() == self.scope.base, "fixture main moved")

    async def check_ref(self, head: str) -> None:
        row = await self.get(self.scope.ref_path)
        require(
            row["ref"] == self.scope.ref and row["object"]["sha"] == head,
            "owned fixture branch moved",
        )

    async def pulls(self, *, opened: bool) -> list[dict[str, Any]]:
        params = {"state": "open" if opened else "all", "per_page": 100}
        if not opened:
            params["head"] = f"mgd43b:{self.scope.branch}"
        rows = await self.get("/pulls", params=params)
        require(isinstance(rows, list), "fixture pull list unavailable")
        return rows

    async def check_pull(self, number: int, head: str, *, state: str = "open") -> None:
        row = await self.get(f"/pulls/{positive_id(number)}")
        require(row["number"] == number and row["draft"] is False, "fixture PR identity changed")
        require_pull(row, self.scope.branch, head, self.scope.base, state)
        require(row["merged"] is False, "fixture PR merge state is unavailable or changed")

    async def compare(self, start: str, end: str) -> dict[str, Any]:
        response = await self.client.get(
            f"{API}/repos/{REPO}/compare/{sha(start)}...{sha(end)}",
            params={"page": 1, "per_page": 100},
            follow_redirects=False,
        )
        require(
            response.status_code == 200 and "next" not in response.links,
            "fixture comparison unavailable or incomplete",
        )
        row = response.json()
        require(
            isinstance(row["files"], list) and len(row["files"]) < 300,
            "fixture comparison files unavailable or incomplete",
        )
        return row

    async def commit_parents(self, revision: str) -> list[str]:
        row = await self.get(f"/git/commits/{sha(revision)}")
        require(row["sha"] == revision, "provider returned a different fixture commit")
        return [sha(parent["sha"]) for parent in row["parents"]]


def edited(data: bytes) -> bytes:
    require(data.startswith(OLD_HEADING), "fixture README heading changed")
    return NEW_HEADING + data[len(OLD_HEADING) :]


async def preflight(fixture: Fixture) -> tuple[bytes, str]:
    scope = fixture.scope
    await fixture.check_repository()
    require(await fixture.pulls(opened=True) == [], "fixture repository already has an open PR")
    compare = await fixture.compare(scope.ancestor, scope.base)
    old, blob = await fixture.content("README.md", scope.ancestor)
    current, _ = await fixture.content("README.md", scope.base)
    suffix = b"\nProjection trial.\n"
    require(
        compare["base_commit"]["sha"] == scope.ancestor
        and compare["merge_base_commit"]["sha"] == scope.ancestor
        and compare["status"] == "ahead"
        and "README.md" in {entry["filename"] for entry in compare["files"]}
        and current == old + suffix,
        "fixture ancestor and README append evidence differ from the reviewed scenario",
    )
    change = edited(old)
    note("branch-update-preflight", ancestor=scope.ancestor, base=scope.base, open_prs=0)
    return change, blob


async def prepare(fixture: Fixture, change: bytes, blob: str) -> tuple[int, str]:
    scope = fixture.scope
    await fixture.check_main()
    created = await fixture._write(
        "POST", "/git/refs", json={"ref": scope.ref, "sha": scope.ancestor}
    )
    require(created.status_code == 201, "fixture branch creation unconfirmed")
    await fixture.check_ref(scope.ancestor)
    await fixture.check_main()
    edit = await fixture._write(
        "PUT",
        "/contents/README.md",
        json={
            "branch": scope.branch,
            "sha": blob,
            "message": "Change the fixture README heading",
            "content": base64.b64encode(change).decode("ascii"),
        },
    )
    require(edit.status_code == 200, "fixture heading edit unconfirmed")
    head = sha(edit.json()["commit"]["sha"])
    await fixture.check_ref(head)
    require(await fixture.commit_parents(head) == [scope.ancestor], "fixture branch changed")
    require((await fixture.content("README.md", head))[0] == change, "fixture edit differs")
    compare = await fixture.compare(scope.ancestor, head)
    require(
        compare["merge_base_commit"]["sha"] == scope.ancestor
        and [entry["filename"] for entry in compare["files"]] == ["README.md"],
        "fixture heading edit exceeded its scope",
    )
    await fixture.check_main()
    require(await fixture.pulls(opened=True) == [], "fixture repository already has an open PR")
    opened = await fixture._write(
        "POST",
        "/pulls",
        json={"title": "Observe one fixture branch update", "head": scope.branch, "base": "main"},
    )
    require(opened.status_code == 201, "fixture PR creation unconfirmed")
    number = positive_id(opened.json()["number"])
    fixture.number = number
    note(
        "branch-update-fixture-created", branch=scope.branch, pr=number, head=head, base=scope.base
    )
    await fixture.check_pull(number, head)
    return number, head


@asynccontextmanager
async def observer(root: Path, *, initialize: bool):
    home = root / "home"
    data = root / "data"
    registry = TokenRegistry(data)
    if initialize:
        home.mkdir(mode=0o700)
    require_private(home, True)
    os.environ["HOME"] = str(home)
    if initialize:
        config = make_config(root, poll_seconds=4)
        token_id = registry.issue(PROJECT, "orchestrator").token_id
        record(root / "observer.json", {"token_id": token_id})
    else:
        config = root / "fixture.toml"
        token_id = read_record(root / "observer.json")["token_id"]
    store = SqliteStore(PROJECT, data)
    try:
        async with stdio(config, token_id) as client:
            yield client, store
    finally:
        await store.close()
        await stop_daemon(data)


async def expect_stale(root: Path, scope: Scope, number: int, head: str) -> None:
    async with observer(root, initialize=True) as (client, store):

        async def read() -> dict[str, Any]:
            return await checked(client, "merge_gate", pr=number)

        verdict = await eventually(
            read,
            lambda row: (
                row["head_sha"] == head
                and bool(row["as_of"])
                and [reason["code"] for reason in row["reasons"]] == ["stale_ci"]
            ),
            "fixture gate did not produce a fresh stale-CI refusal",
        )
        projection = await store.read_merge_gate(REPO, number)
        comparison = projection.comparison
        require(
            comparison is not None
            and comparison.complete
            and comparison.attributes.get("main_tip") == scope.base
            and comparison.attributes.get("head_sha") == head,
            "fixture gate comparison pins changed",
        )
        rows = await store.events_after(0, 1000)
        require(
            len(rows) < 1000
            and any(
                event.kind == "core.merge_verdict"
                and event.fields["number"] == number
                and bool(event.fields.get("as_of"))
                and all(
                    event.fields.get(key) == verdict[key]
                    for key in ("head_sha", "verdict", "reasons", "exit_class")
                )
                for event in rows
            ),
            "fixture verdict journal did not match the gate read",
        )
        note(
            "branch-update-stale-ci", pr=number, head=head, base=scope.base, as_of=verdict["as_of"]
        )


async def submit(fixture: Fixture, root: Path) -> None:
    scope = fixture.scope
    change, blob = await preflight(fixture)
    await fixture.ready_ci(scope.base, "main", "push")
    record(root / "scope.json", asdict(scope))
    note("branch-update-fixture-scope", branch=scope.branch, base=scope.base, source=scope.source)
    number, head = await prepare(fixture, change, blob)
    await fixture.ready_ci(head, scope.branch, "pull_request")
    await expect_stale(root, scope, number, head)
    await fixture.check_pull(number, head)
    require(
        [row["number"] for row in await fixture.pulls(opened=True)] == [number],
        "fixture PR ownership changed before submission",
    )
    await fixture.check_ref(head)
    await fixture.check_main()
    record(root / "attempt.json", {"pr": number, "head": head})
    note("branch-update-attempt-consumed", pr=number, head=head, base=scope.base)
    try:
        response = await fixture._write(
            "PUT", f"/pulls/{number}/update-branch", json={"expected_head_sha": head}
        )
    except httpx.RequestError:
        note("branch-update-response-unknown", pr=number)
        return
    record(root / "response.json", {"status": response.status_code})
    note("branch-update-response", pr=number, status=response.status_code)


async def resulting_head(fixture: Fixture, number: int, head: str) -> str:
    scope = fixture.scope

    async def read() -> str:
        await fixture.check_main()
        readback = await fixture.get(f"/pulls/{number}")
        result = sha(readback["head"]["sha"])
        require(readback["merged"] is False and readback["draft"] is False, "fixture PR changed")
        require_pull(readback, scope.branch, result, scope.base, "open")
        return result

    result = await eventually(
        read, lambda candidate: candidate != head, "update observation timed out"
    )
    await fixture.check_ref(result)
    require(
        await fixture.commit_parents(result) == [head, scope.base],
        "observed fixture head lacks both planned parents",
    )
    expected = edited((await fixture.content("README.md", scope.base))[0])
    require((await fixture.content("README.md", result))[0] == expected, "merged README differs")
    compare = await fixture.compare(scope.base, result)
    require(
        compare["merge_base_commit"]["sha"] == scope.base
        and [entry["filename"] for entry in compare["files"]] == ["README.md"],
        "branch update changed files outside the fixture README",
    )
    await fixture.check_main()
    return result


async def observe(fixture: Fixture, root: Path) -> None:
    scope = fixture.scope
    require(read_record(root / "scope.json") == asdict(scope), "fixture restart scope changed")
    attempt = read_record(root / "attempt.json")
    number, head = positive_id(attempt["pr"]), sha(attempt["head"])
    fixture.number = number
    await fixture.check_repository()
    result = await resulting_head(fixture, number, head)
    async with observer(root, initialize=False) as (_, store):

        async def read():
            return [
                event
                for event in await store.events_after(0, 1000)
                if event.kind == PR_HEAD_CHANGED
                and event.fields["number"] == number
                and event.fields["head_sha"] == result
            ]

        await eventually(
            read, lambda rows: len(rows) == 1, "restart did not project the updated head"
        )
    note("branch-update-observed", pr=number, head_before=head, head_after=result, base=scope.base)
    response = root / "response.json"
    require(
        response.is_file() and read_record(response).get("status") == 202,
        "provider response uncertain; retained fixture for operator reconciliation",
    )
    await fixture.check_main()
    await fixture.check_pull(number, result)
    await fixture.check_ref(result)
    require(
        [row["number"] for row in await fixture.pulls(opened=False)] == [number],
        "cleanup fixture ownership is ambiguous",
    )
    record(root / "cleanup.json", {"pr": number, "head": result})
    closed = await fixture._write("PATCH", f"/pulls/{number}", json={"state": "closed"})
    require(closed.status_code == 200, "fixture PR closure unconfirmed")
    await fixture.check_pull(number, result, state="closed")
    await fixture.check_ref(result)
    await fixture.check_main()
    deleted = await fixture._write("DELETE", f"/git/refs/heads/{scope.branch}")
    require(deleted.status_code == 204, "fixture branch deletion unconfirmed")
    require(
        await fixture.get(scope.ref_path, optional=True) is None
        and fixture.denied.get(scope.ref_path) == 404,
        "fixture branch still exists or deletion is unreadable",
    )
    await fixture.check_main()
    note("branch-update-fixture-cleaned", pr=number, branch=scope.branch, unchanged_main=scope.base)


async def run(args: argparse.Namespace, scope: Scope) -> None:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
    if args.phase != "preflight":
        root: Path = args.directory
        require_private(root, True)
        require(TOKEN_ENV not in os.environ, "fixture credential environment override is set")
        path = root / "github-token"
        require_private(path, False)
        token = path.read_text(encoding="utf-8").strip()
        require(
            re.fullmatch(r"github_pat_[A-Za-z0-9_]+", token), "scoped fine-grained token required"
        )
        headers["Authorization"] = f"Bearer {token}"
    async with (
        asyncio.timeout(380),
        httpx.AsyncClient(
            headers=headers, follow_redirects=False, trust_env=False, timeout=10
        ) as http,
    ):
        fixture = Fixture(http, scope, args.phase)
        if args.phase == "preflight":
            await preflight(fixture)
        elif args.phase == "submit":
            await submit(fixture, args.directory)
        else:
            await observe(fixture, args.directory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("preflight", "submit", "observe"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--ancestor", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        scope = scope_for(args, os.environ)
        asyncio.run(run(args, scope))
    except AcceptanceError as exc:
        note("branch-update-stopped", phase=args.phase, reason=str(exc))
        return 1
    except (httpx.RequestError, TimeoutError, OSError, KeyError, ValueError, TypeError) as exc:
        note("branch-update-stopped", phase=args.phase, reason=type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
