"""Exercise the two production work-item sources against one read-only fixture."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import httpx
from deploy.bridge_smoke import ROOT
from deploy.epic_two_archived import REPO, ArchiveReadError, ProviderReads, Reader

from plotroom.adapters.github import GITHUB_API, LocalGitHubCredential
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.issue_details import fetch_issue_tree, fetch_issues
from plotroom.adapters.github.read import GitHubReadClient, Row, obj, rows
from plotroom.adapters.github.ticket_tree import GitHubTicketTree
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.work_items_bmad import read_ticket_body, read_ticket_tree
from plotroom.adapters.work_items_github_issues import GitHubIssuesWorkItems
from plotroom.app.clock import SystemClock
from plotroom.core.catalog import build_catalog
from plotroom.core.events import format_time
from plotroom.core.github_projection import ISSUE_TREE, apply_resource
from plotroom.core.ledger import Ledger
from plotroom.ports.work_items import WorkItem

PREFIX = f"/repos/{REPO}"
TREE_COMMIT = "9a6ce8a89a15046c18dbfa5d7d2af88bb0edbc14"
ROOT_TREE = "444235a0fb71f25c289a384ee521d6a117c8f1e9"
INITIATIVE_TREE = "27069e9cbf8f0f172fa4151f9d89c4161170e792"
TREE = "initiative-demo/tickets/epic-inbox/tickets"
EXPECTED_TREE = (WorkItem("1.1", "Fixture ticket"), WorkItem("1.2", "Fixture follow-up"))
TREE_READS = {
    PREFIX,
    f"{PREFIX}/branches/main",
    f"{PREFIX}/git/trees/{INITIATIVE_TREE}?recursive=1",
    *(
        f"{PREFIX}/git/trees/{sha}"
        for sha in (
            ROOT_TREE,
            INITIATIVE_TREE,
            "d7b4d8eca345057ce6e18044bb564a6b254ea6b8",
            "a1d7fee827306c333da4f58ca78e4df13889553d",
            "4fd94ef9884b9fa7d9017e7f3d73de1795d90420",
            "46f53bc5c9fc3e2f30bf349a91d994db356f42bf",
            "4793add8547d66fa5db622e2a6c6f706818fa57e",
        )
    ),
    *(
        f"{PREFIX}/git/blobs/{sha}"
        for sha in (
            "fcb32d84c3cbaf43ba3db1ec859ccb7aae0316f3",
            "1cb9debe95a5ecac8f666a520bdc54aaa41d4532",
            "22b08eb96b82f6f0aa91346eed7d5f4f21962214",
            "a47c653fa91ed2792400ee77e76f0ffbfecae514",
            "7b3d1369e8e6405fdfa64a497b1c384df580b7c9",
        )
    ),
}
MARKER = "disposable-work-item-fixture"
ISSUE_PAGE = "state=all&per_page=100&page=1"
RELATION_PAGE = "per_page=100&page=1"
RELATIONS = ("sub_issues", "dependencies/blocked_by")
REFINED_METADATA = re.compile(r"\A```yaml[ \t]*\r?\n(.*?)^```[ \t]*\r?$", re.M | re.S)


def tree_scope(method: str, path: str, body: bytes) -> str:
    if method != "GET" or body or path not in TREE_READS:
        raise ArchiveReadError("request outside the pinned fixture tree read scope")
    return path[1:]


def request_target(request: httpx.Request) -> str:
    if request.url.scheme != "https" or request.url.host != "api.github.com":
        raise ArchiveReadError("work-item fixture request was not sent to the GitHub API")
    return request.url.raw_path.decode("ascii")


async def tree_witness(read: Reader) -> tuple[WorkItem, ...]:
    async def transfer(request: httpx.Request) -> httpx.Response:
        path = request_target(request)
        tree_scope(request.method, path, request.content)
        headers, body = await asyncio.to_thread(read, "GET", path, b"")
        if path == f"{PREFIX}/branches/main":
            commit = obj(obj(json.loads(body))["commit"])
            tree = obj(obj(commit["commit"])["tree"])
            if (commit["sha"], tree["sha"]) != (TREE_COMMIT, ROOT_TREE):
                raise ArchiveReadError("pinned fixture default-branch tree changed")
        return httpx.Response(200, headers=headers, content=body)

    source = GitHubTicketTree(
        GitHubReadClient(GITHUB_API, REPO),
        LocalGitHubCredential("FIXTURE", {"FIXTURE": MARKER}, None),
        TREE,
        partial(read_ticket_tree, ROOT),
        read_body=partial(read_ticket_body, ROOT),
        transport=httpx.MockTransport(transfer),
    )
    ready = await source.ready_items()
    if ready != EXPECTED_TREE:
        raise ArchiveReadError("the pinned repo-store fixture returned different ready tickets")
    return ready


class IssueScope:
    def __init__(self, milestone: int, child: int) -> None:
        if not all(type(value) is int and 0 < value < 2**63 for value in (milestone, child)):
            raise ValueError("milestone and child must be positive GitHub numbers")
        self.milestone_path = f"{PREFIX}/milestones/{milestone}"
        self.child_path = f"{PREFIX}/issues/{child}"
        self.page_path = f"{PREFIX}/issues?milestone={milestone}&{ISSUE_PAGE}"
        self.relations: set[str] = set()

    def set_parent(self, parent: int, child: int) -> None:
        self.relations = {
            f"{PREFIX}/issues/{number}/{relation}?{RELATION_PAGE}"
            for number in (parent, child)
            for relation in RELATIONS
        }

    def endpoint(self, method: str, path: str, body: bytes) -> str:
        if (
            method != "GET"
            or body
            or path
            not in {PREFIX, self.milestone_path, self.child_path, self.page_path, *self.relations}
        ):
            raise ArchiveReadError("request outside the supplied fixture milestone read scope")
        return path[1:]


def issue_record(row: Row, milestone: int, labels: set[str]) -> bool:
    native_labels = {label.get("name") for label in rows(row["labels"])}
    assigned = row.get("milestone")
    return (
        "pull_request" not in row
        and row.get("state") == "open"
        and labels <= native_labels
        and (assigned is None or obj(assigned).get("number") == milestone)
    )


def issue_identity(row: Row) -> tuple[object, ...]:
    milestone = row.get("milestone")
    return (
        row.get("number"),
        row.get("title"),
        row.get("state"),
        row.get("state_reason"),
        row.get("updated_at"),
        None if milestone is None else obj(milestone).get("number"),
        {label.get("name") for label in rows(row["labels"])},
    )


async def issues_witness(read: Reader, scope: IssueScope, milestone: int, child: int) -> WorkItem:
    async def fetch(path: str) -> tuple[dict[str, str], bytes]:
        scope.endpoint("GET", path, b"")
        return await asyncio.to_thread(read, "GET", path, b"")

    _, milestone_bytes = await fetch(scope.milestone_path)
    native_milestone = obj(json.loads(milestone_bytes))
    if (native_milestone.get("number"), native_milestone.get("state")) != (milestone, "open"):
        raise ArchiveReadError("the supplied fixture milestone is not open")

    _, child_bytes = await fetch(scope.child_path)
    native_child = obj(json.loads(child_bytes))
    body = native_child.get("body")
    metadata = REFINED_METADATA.search(body[:8192]) if isinstance(body, str) else None
    if (
        native_child.get("number") != child
        or not issue_record(native_child, milestone, {"story", "backlog"})
        or not isinstance(body, str)
        or metadata is None
        or not re.search(r"^refined:[ \t]*true[ \t]*\r?$", metadata[1], re.M)
    ):
        raise ArchiveReadError("the supplied fixture child is not an open refined backlog story")

    headers, page = await fetch(scope.page_path)
    issues = rows(json.loads(page))
    if any(name.lower() == "link" for name in headers):
        raise ArchiveReadError("the fixture milestone issue list exceeds the scoped page")
    parents = [issue for issue in issues if issue.get("number") != child]
    matched_children = [issue for issue in issues if issue.get("number") == child]
    if (
        len(parents) != 1
        or not issue_record(parents[0], milestone, {"epic"})
        or parents[0].get("milestone") is None
        or (
            len(matched_children) != 1
            and not (not matched_children and native_child.get("milestone") is None)
        )
    ):
        raise ArchiveReadError(
            "the fixture milestone does not contain just the expected epic and story"
        )
    parent = parents[0].get("number")
    if type(parent) is not int or not 0 < parent < 2**63:
        raise ArchiveReadError("the fixture epic number is invalid")
    if matched_children and (
        native_child.get("milestone") is None
        or issue_identity(matched_children[0]) != issue_identity(native_child)
    ):
        raise ArchiveReadError("the selected issue changed between its native and milestone reads")
    scope.set_parent(parent, child)
    if not matched_children:
        issues.append(native_child)

    async def transfer(request: httpx.Request) -> httpx.Response:
        path = request_target(request)
        if request.method != "GET" or request.content:
            raise ArchiveReadError("the fixture Issues projection permits read requests only")
        if path == f"{PREFIX}/issues?{ISSUE_PAGE}":
            return httpx.Response(200, json=issues)
        if path == f"{PREFIX}/milestones?{ISSUE_PAGE}":
            return httpx.Response(200, json=[native_milestone])
        if path not in scope.relations:
            raise ArchiveReadError("request outside the selected issue relationships")
        response_headers, response_body = await fetch(path)
        if any(name.lower() == "link" for name in response_headers):
            raise ArchiveReadError("the selected issue relationships exceed the scoped page")
        # The production projection omits foreign sub-issues. Check the native
        # shape before projection can filter out an unexpected child.
        related = rows(json.loads(response_body))
        if path == f"{PREFIX}/issues/{parent}/sub_issues?{RELATION_PAGE}":
            if [(row.get("number"), row.get("repository_url")) for row in related] != [
                (child, GITHUB_API + PREFIX)
            ]:
                raise ArchiveReadError("the fixture epic does not have exactly the selected child")
        elif related:
            raise ArchiveReadError("the fixture story or epic has unexpected relationships")
        return httpx.Response(200, headers=response_headers, content=response_body)

    api = GitHubReadClient(GITHUB_API, REPO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transfer), trust_env=False) as http:
        observed = await fetch_issues(api, http, MARKER, None)
        tree = await fetch_issue_tree(api, http, MARKER, observed, None)
    relationships = {
        record.fields["number"]: record.fields
        for record in tree.records
        if record.kind == "issue_metadata"
    }
    if (
        not tree.complete
        or set(relationships) != {parent, child}
        or relationships[parent]["sub_issues"] != [child]
        or relationships[child]["sub_issues"]
        or relationships[parent]["blocked_by"]
        or relationships[child]["blocked_by"]
    ):
        raise ArchiveReadError(
            "the fixture issue relationships do not match the bounded epic and story"
        )

    with tempfile.TemporaryDirectory(prefix="plotroom-epic-two-issues-") as directory:
        root = Path(directory)
        store_config = root / "tickets.toml"
        store_config.write_text(f'[tickets]\nstore = "github"\nrepo = "{REPO}"\n', encoding="utf-8")
        store = SqliteStore("mirror", root)
        await store.migrate()
        clock = SystemClock()
        ledger = Ledger(store, build_catalog(github_repository=REPO), clock, github_repository=REPO)
        ledger.start()
        try:
            await ledger.run(
                "system",
                lambda uow: apply_resource(
                    uow, REPO, ISSUE_TREE, "", tree, format_time(datetime.now(UTC))
                ),
            )
            poller = GitHubDetailPoller(
                ledger, clock, api, LocalGitHubCredential("FIXTURE", {"FIXTURE": MARKER}, None)
            )
            source = GitHubIssuesWorkItems(
                poller.issue_tree,
                ROOT / ".agents/skills/bmad-agent-po/config/github-ticketing.toml",
                store_config,
                REPO,
                milestone,
            )
            ready = await source.ready_items()
            mirrored = await source.body(str(child))
        finally:
            await ledger.stop()
            await store.close()
    if ready != (WorkItem(str(child), native_child["title"]),):
        raise ArchiveReadError(
            "the configured Issues view did not return exactly the supplied ready child"
        )
    if mirrored is None or mirrored.encode("utf-8") != body.encode("utf-8"):
        raise ArchiveReadError("the ready child's body differs from its native GitHub body")
    return ready[0]


async def work_item_mirrors(
    milestone: int | None, child: int | None, report: Callable[[str], None]
) -> bool:
    provider = ProviderReads(tree_scope)
    await asyncio.to_thread(provider.preflight_public)
    await tree_witness(provider.read)
    report("Repo-store mirror PASS — 1.1 Fixture ticket, then 1.2 Fixture follow-up.")
    if milestone is None or child is None:
        report("GitHub Issues mirror BLOCKED — supply verified milestone and child numbers.")
        return False
    scope = IssueScope(milestone, child)
    provider = ProviderReads(scope.endpoint)
    await asyncio.to_thread(provider.preflight_public)
    await issues_witness(provider.read, scope, milestone, child)
    report(f"GitHub Issues mirror PASS — one ready child #{child} with the exact native body.")
    return True
