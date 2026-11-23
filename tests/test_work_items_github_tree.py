import base64
import hashlib
import shutil
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path, PurePosixPath

import httpx
import pytest

from fakes import FixedClock
from plotroom.adapters.github import LocalGitHubCredential
from plotroom.adapters.github.read import GitHubReadClient, Row
from plotroom.adapters.github.ticket_tree import GitHubTicketTree, validate_tree_path
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.work_items_bmad import BmadTicketTree, read_ticket_tree
from plotroom.app.runtime import ProjectRuntime
from plotroom.core.catalog import build_catalog
from plotroom.core.ledger import Ledger
from plotroom.ports.work_items import WorkItem

REPOSITORY = "fixture/read-only"
PREFIX = f"/repos/{REPOSITORY}"
TOKEN = "fixture-secret"
SOURCE = "nested/initiative-demo/tickets/epic-followup"
STORE_CONFIG = "nested/_bmad/custom/ticketing-store-config.toml"


def object_id(content: bytes) -> str:
    return hashlib.sha1(content, usedforsecurity=False).hexdigest()


@dataclass
class GitFixture:
    roots: dict[str, str] = field(default_factory=lambda: {})
    trees: dict[str, list[Row]] = field(default_factory=lambda: {})
    blobs: dict[str, Row] = field(default_factory=lambda: {})
    requests: list[str] = field(default_factory=lambda: [])
    head: str = ""
    truncated: bool = False
    repo_error: httpx.Response | None = None

    def snapshot(self, root: Path) -> str:
        sha = self._snapshot_dir(root)
        commit = object_id(f"commit:{sha}".encode())
        self.roots[commit] = sha
        return commit

    def _snapshot_dir(self, root: Path) -> str:
        entries: list[Row] = []
        for path in sorted(root.iterdir()):
            if path.is_dir():
                entry = {"type": "tree", "mode": "040000", "sha": self._snapshot_dir(path)}
            else:
                content = path.read_bytes()
                sha = object_id(content)
                self.blobs[sha] = {
                    "encoding": "base64",
                    "size": len(content),
                    "content": base64.encodebytes(content).decode(),
                }
                entry = {"type": "blob", "mode": "100644", "sha": sha, "size": len(content)}
            entries.append({"path": path.name, **entry})
        sha = object_id(repr(entries).encode())
        self.trees[sha] = entries
        return sha

    def _entries(self, sha: str, recursive: bool) -> list[Row]:
        rows: list[Row] = []
        for row in self.trees[sha]:
            rows.append(row)
            if recursive and row["type"] == "tree":
                rows.extend(
                    {**child, "path": f"{row['path']}/{child['path']}"}
                    for child in self._entries(row["sha"], recursive)
                )
        return rows

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.url.host == "github.test"
        path = request.url.path.removeprefix(PREFIX)
        self.requests.append(path)
        if path == "":
            return self.repo_error or httpx.Response(
                200, json={"default_branch": "release/default"}
            )
        if path == "/branches/release/default":
            assert request.url.raw_path == (PREFIX + "/branches/release%2Fdefault").encode()
            sha = self.roots[self.head]
            return httpx.Response(
                200, json={"commit": {"sha": self.head, "commit": {"tree": {"sha": sha}}}}
            )
        sha = path.split("/")[-1]
        if path.startswith("/git/trees/"):
            recursive = request.url.params.get("recursive") == "1"
            return httpx.Response(
                200,
                json={
                    "truncated": recursive and self.truncated,
                    "tree": self._entries(sha, recursive),
                },
            )
        if path.startswith("/git/blobs/"):
            return httpx.Response(200, json=self.blobs[sha])
        pytest.fail(f"unexpected fixture request: {request.url}")

    def source(self, installation: Path, path: str = SOURCE) -> GitHubTicketTree:
        return GitHubTicketTree(
            GitHubReadClient("https://github.test", REPOSITORY),
            LocalGitHubCredential("FIXTURE_TOKEN", {"FIXTURE_TOKEN": TOKEN}, None),
            path,
            partial(read_ticket_tree, installation),
            transport=httpx.MockTransport(self.handle),
        )


def write(root: Path, name: str, text: str) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture
def checkout_and_reader(tmp_path: Path) -> tuple[Path, Path]:
    installation = tmp_path / "trusted"
    script = Path("_bmad/method/scripts/tickets.py")
    installed = installation / script
    installed.parent.mkdir(parents=True)
    shutil.copyfile(script, installed)
    checkout = tmp_path / "checkout"
    write(checkout, STORE_CONFIG, '[tickets]\nstore = "repo"\n')
    write(
        checkout,
        "_bmad/custom/ticketing-store-config.toml",
        '[tickets]\nstore = "github"\n',
    )
    write(
        checkout,
        "nested/_bmad/scripts/config_utils.py",
        "raise AssertionError('the checkout supplied code')",
    )
    initiative = "nested/initiative-demo"
    write(checkout, f"{initiative}/notes/large.md", "x" * (512 * 1024 + 1))
    write(
        checkout,
        f"{initiative}/initiative-demo.md",
        "---\ntype: initiative\n---\n",
    )
    write(
        checkout,
        f"{initiative}/tickets/tickets.toml",
        '[[epic]]\nid = 1\nslug = "epic-prep"\n[[epic]]\nid = 2\nslug = "epic-followup"\n',
    )
    prep = f"{initiative}/tickets/epic-prep"
    write(checkout, f"{prep}/epic-prep.md", "---\ntype: epic\n---\n")
    write(
        checkout,
        f"{prep}/tickets/tickets.toml",
        '[[entry]]\nid = 1\ntype = "story"\ntitle = "Prerequisite"\n',
    )
    write(
        checkout,
        f"{SOURCE}/epic-followup.md",
        "---\ntype: epic\n---\n",
    )
    write(
        checkout,
        f"{SOURCE}/tickets/tickets.toml",
        """
[[entry]]
id = 1
type = "story"
title = "Always ready"
[[entry]]
id = 2
type = "story"
title = "Ready after review"
after = ["1.1"]
""",
    )
    return checkout, installation


async def test_default_branch_tick_matches_checkout_and_preserves_last_good_view(
    tmp_path: Path, checkout_and_reader: tuple[Path, Path]
) -> None:
    checkout, installation = checkout_and_reader
    github = GitFixture()
    github.head = github.snapshot(checkout)
    source = github.source(installation, f"{SOURCE}/tickets")
    local = BmadTicketTree(checkout / SOURCE / "tickets", installation)
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    ledger = Ledger(store, build_catalog(), clock)
    ledger.start()
    runtime = ProjectRuntime(ledger, clock, tick_seconds=5, action_attempts=3, work_items=source)

    async def tick() -> tuple[WorkItem, ...]:
        clock.advance(5)
        assert await runtime.tick_if_due()
        return await ledger.ready_work_items()

    try:
        first = await tick()
        assert first == await local.ready_items() == (WorkItem("2.1", "Always ready"),)
        assert await tick() == first
        assert github.requests[-2:] == ["", "/branches/release/default"]

        write(
            checkout,
            "nested/initiative-demo/tickets/epic-prep/tickets/story-prep-plan.md",
            "---\ntype: feature\nticket: 1\nstatus: built\n---\n",
        )
        github.head = github.snapshot(checkout)
        github.truncated = True
        assert await tick() == first
        github.truncated = False
        assert (
            await tick()
            == await local.ready_items()
            == (
                *first,
                WorkItem("2.2", "Ready after review"),
            )
        )
        # A fresh source can reconstruct the view using only the pinned provider objects.
        assert await github.source(installation).ready_items() == await local.ready_items()
        script = checkout / "nested/_bmad/scripts/config_utils.py"
        assert f"/git/blobs/{object_id(script.read_bytes())}" not in github.requests
    finally:
        await ledger.stop()
        await store.close()


async def test_rate_limit_stops_requests_for_retry_window(
    checkout_and_reader: tuple[Path, Path],
) -> None:
    checkout, installation = checkout_and_reader
    github = GitFixture(repo_error=httpx.Response(429, headers={"Retry-After": "100"}))
    github.head = github.snapshot(checkout)
    source = github.source(installation)
    with pytest.raises(ValueError, match="rate limit"):
        await source.ready_items()
    github.repo_error = None
    with pytest.raises(ValueError, match="rate limit"):
        await source.ready_items()
    assert github.requests == [""]


async def test_default_branch_requires_repo_store(
    checkout_and_reader: tuple[Path, Path],
) -> None:
    checkout, installation = checkout_and_reader
    write(checkout, STORE_CONFIG, '[tickets]\nstore = "github"\n')
    github = GitFixture()
    github.head = github.snapshot(checkout)
    with pytest.raises(ValueError, match="repo store"):
        await github.source(installation).ready_items()


@pytest.mark.parametrize(
    ("metadata", "error"),
    [
        ({"path": "../story-escape.md"}, "repository-relative"),
        ({"mode": "120000"}, "regular files"),
        ({"size": 2_000_000}, "size limit"),
    ],
)
async def test_unsafe_or_excessive_blob_metadata_is_refused(
    checkout_and_reader: tuple[Path, Path], metadata: Row, error: str
) -> None:
    checkout, installation = checkout_and_reader
    github = GitFixture()
    github.head = github.snapshot(checkout)
    files = next(
        entries
        for entries in github.trees.values()
        if any(entry["path"] == "epic-followup.md" for entry in entries)
    )
    entry = next(entry for entry in files if entry["path"] == "epic-followup.md")
    entry.update(metadata)
    with pytest.raises(ValueError, match=error):
        await github.source(installation).ready_items()
    assert not any("/git/blobs/" in path for path in github.requests)


@pytest.mark.parametrize("path", ["", "../tree", "/tree", "a/../tree", "./tree", r"a\tree"])
def test_invalid_tree_path_is_refused(path: str) -> None:
    with pytest.raises(ValueError, match="repository-relative"):
        validate_tree_path(path)


def test_root_and_nested_paths_are_canonical() -> None:
    assert validate_tree_path(".") == PurePosixPath(".")
    assert validate_tree_path("docs/ticket tree") == PurePosixPath("docs/ticket tree")
