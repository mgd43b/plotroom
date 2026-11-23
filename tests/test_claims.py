"""Focused tests of normalization and projection reconciliation."""

from dataclasses import replace
from typing import Any

import pytest

from fakes import FixedClock, MemoryStore
from plotroom.core.catalog import build_catalog
from plotroom.core.claims import (
    PR_CLAIM_OVERLAP,
    compile_glob,
    literal_prefix,
    normalize_glob,
)
from plotroom.core.events import LedgerError
from plotroom.core.github import PULL_REQUESTS, ObservedPull, apply_pull_poll
from plotroom.core.github_projection import FILES, GitHubRecord, GitHubResource, apply_resource
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork

REPO = "fixture/repo"
AT = "2026-11-22T12:00:00Z"


@pytest.mark.parametrize(
    ("pattern", "normalized", "prefix", "matched", "unmatched"),
    [
        ("//src/./a//**", "src/a/**", "src/a/", "src/a/.hidden/x.py", "src/abc/x.py"),
        ("config.yml  ", "config.yml", "", "src/config.yml", "src/config.yml.bak"),
        ("/config.yml", "/config.yml", "config.yml/", "config.yml", "src/config.yml"),
        ("logs/", "logs/", "", "build/logs/output", "build/logs"),
        ("src/*.py", "src/*.py", "src/", "src/a.py", "src/a/a.py"),
        ("src/***/x.py", "src/***/x.py", "src/", "src/x.py", "other/src/x.py"),
        ("src/[[:digit:]].py", "src/[[:digit:]].py", "src/", "src/2.py", "src/x.py"),
        ("src/[!x]file", "src/[!x]file", "src/", "src/bfile", "src/xfile"),
        ("src/[]]", "src/[]]", "src/", "src/]", "src/x"),
        ("src/[!/]", "src/[!/]", "src/", "src/x", "other/x"),
        ("file[!/].py", "/file[!/].py", "file", "file1.py", "src/file1.py"),
        ("src/??", "src/??", "src/", "src/é", "src/z"),
        ("src/é", "src/é", "src/é/", "src/é", "src/e"),
        (r"src\/file", "src/file", "src/file/", "src/file", "src/other"),
        (r"src/\*.txt", r"src/\*.txt", "src/*.txt/", "src/*.txt", "src/a.txt"),
        (r"\!config", r"\!config", "", "nested/!config", "nested/config"),
        (
            "/!generated/schema.json",
            "/!generated/schema.json",
            "!generated/schema.json/",
            "!generated/schema.json",
            "other/!generated/schema.json",
        ),
    ],
)
def test_glob_normalization_and_gitignore_boundaries(
    pattern: str, normalized: str, prefix: str, matched: str, unmatched: str
) -> None:
    assert normalize_glob(pattern) == normalized
    assert normalize_glob(normalized) == normalized
    assert literal_prefix(normalized) == prefix
    matcher = compile_glob(normalized)
    assert matcher.matches(matched)
    assert not matcher.matches(unmatched)


@pytest.mark.parametrize("pattern", ["!", "!src/a", "src/../a", "/", "", "a\0b", "a\\"])
def test_invalid_claim_globs(pattern: str) -> None:
    with pytest.raises(LedgerError) as error:
        normalize_glob(pattern)
    assert error.value.code == "invalid"


def test_unterminated_bracket_class_is_refused() -> None:
    with pytest.raises(LedgerError, match="unterminated bracket class"):
        compile_glob("src/[!]")


def files(number: int, head: str, *paths: str) -> GitHubResource:
    rows = tuple(
        GitHubRecord(
            path,
            "pull_file",
            {"number": number, "head_sha": head, "path": path, "status": "modified"},
        )
        for path in paths
    )
    return GitHubResource(rows, attributes={"head_sha": head})


async def test_file_provenance_refresh_preserves_claims_without_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[str] = []
    original = UnitOfWork.pull_overlaps_changed

    def track(uow: UnitOfWork) -> None:
        changes.append("files changed")
        original(uow)

    ledger = Ledger(MemoryStore(), build_catalog(), FixedClock(), github_repository=REPO)
    ledger.start()
    try:
        observed = ObservedPull(1, "branch", "A", REPO, "open", AT, "")
        await ledger.run("system", lambda uow: apply_pull_poll(uow, REPO, None, (observed,), AT))
        monkeypatch.setattr(UnitOfWork, "pull_overlaps_changed", track)
        initial = files(1, "A", "src/first.py")
        refreshed = replace(initial, attributes={**initial.attributes, "base_sha": "new"})
        for observation in (initial, refreshed):
            await ledger.run(
                "system",
                lambda uow, data=observation: apply_resource(uow, REPO, FILES, "1", data, AT),
            )
        assert changes == ["files changed"]
        resource = await ledger.github_resource(REPO, FILES, "1")
        assert resource is not None
        assert resource.attributes == refreshed.attributes
    finally:
        await ledger.stop()


async def test_stale_resources_and_binding_changes_reconcile_all_open_prs() -> None:
    workers = frozenset({"alice", "bob"})
    ledger = Ledger(MemoryStore(), build_catalog(workers), FixedClock(), github_repository=REPO)
    ledger.start()

    async def observe(*pulls: ObservedPull) -> None:
        baseline = await ledger.fetch_cursor(REPO, PULL_REQUESTS)
        assert await ledger.run(
            "system", lambda uow: apply_pull_poll(uow, REPO, baseline, pulls, AT)
        )

    async def save(number: int, data: GitHubResource) -> None:
        await ledger.run(
            "system", lambda uow: apply_resource(uow, REPO, FILES, str(number), data, AT)
        )

    async def claim_items() -> list[dict[str, Any]]:
        return [
            item.context
            for item in (await ledger.read_board("orchestrator"))[1]
            if item.kind == PR_CLAIM_OVERLAP
        ]

    async def start(lane: str, branch: str) -> None:
        await ledger.execute(
            "orchestrator",
            "lane_start",
            lane,
            {"lane": lane, "branch": branch, "worker": lane, "work_items": []},
        )

    try:
        await start("alice", "main-work")
        numbers = tuple(range(1, 11))
        await observe(
            *(
                ObservedPull(number, f"branch{number}", "A", REPO, "open", AT, "")
                for number in numbers
            )
        )
        for number in numbers:
            await save(number, files(number, "A", f"src/{number}.py"))
        await ledger.execute(
            "worker:alice",
            "claim",
            "claim",
            {"lane": "alice", "globs": ["src/**"], "reason": "test"},
        )
        assert sorted(item["prs"][0] for item in await claim_items()) == list(numbers)
        await start("bob", "branch10")
        tenth = next(item for item in await claim_items() if item["prs"] == [10])
        assert tenth["lanes"] == ["alice", "bob"]
        await observe(ObservedPull(10, "branch10", "B", REPO, "open", "2026-11-22T12:00:01Z", ""))
        await save(10, files(10, "A", "src/10.py"))
        assert all(item["prs"] != [10] for item in await claim_items())
        await observe(ObservedPull(10, "branch10", "A", REPO, "open", "2026-11-22T12:00:02Z", ""))
        assert all(item["prs"] != [10] for item in await claim_items())
        await save(10, files(10, "A", "src/10.py"))
        assert tenth in await claim_items()
        await ledger.execute("orchestrator", "lane_archive", "archive", {"lane": "bob"})
        detached = next(item for item in await claim_items() if item["prs"] == [10])
        assert detached["lanes"] == ["alice"]
        await ledger.execute(
            "worker:alice", "release", "release", {"lane": "alice", "globs": ["src/**"]}
        )
        assert await claim_items() == []
    finally:
        await ledger.stop()
