"""Exercise JUnit reads with synthetic API metadata and a loopback archive endpoint."""

import asyncio
import hashlib
import io
import logging
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import create_autospec

import httpx
import pytest

from fakes import FixedClock
from plotroom.adapters.github import (
    GitHubPoller,
    GitHubRateLimited,
    LocalGitHubCredential,
    PullRequestFetcher,
)
from plotroom.adapters.github import (
    details as details_adapter,
)
from plotroom.adapters.github.ci import (
    JUNIT_MISS_REFRESHES,
    JUNIT_UNAVAILABLE_REFRESHES,
    JUnitBudget,
    fetch_checks,
)
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.junit import (
    MAX_ARCHIVE,
    JUnitReader,
    _download_url,  # pyright: ignore[reportPrivateUsage]
)
from plotroom.adapters.github.junit_xml import MAX_XML, junit_evidence
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.core.catalog import build_catalog
from plotroom.core.github_projection import CHECKS, GitHubRecord, GitHubResource
from plotroom.core.ledger import Ledger
from plotroom.core.merge_flakes import (
    FLAKE_EVENT,
    MAIN_FLAKE_EVENT,
    main_pairs,
    prior_check_failures,
    record_main_flakes,
    record_same_head,
)
from plotroom.core.unit_of_work import UnitOfWork
from test_merge_flakes import (
    FAILED_MAIN,
    FIRST,
    MAIN_SHA,
    REPO,
    FlakeFixture,
    annotation,
    check,
    passed_test,
    status,
)

RESULTS = {
    101: (FIRST, "same-head", "failure", "test_same"),
    102: (FIRST, "same-head", "success", "test_same"),
    103: (FIRST, "outside-files", "failure", "test_outside"),
    104: (FAILED_MAIN, "test", "failure", "test_main"),
    105: (MAIN_SHA, "test", "success", "test_main"),
    106: (MAIN_SHA, "unrelated", "success", "test_quiet"),
}
RUNS = {FIRST: 300, FAILED_MAIN: 200, MAIN_SHA: 201}
PRIVATE_MARKER = "synthetic-raw-report-only"


def zipped(document: str | bytes, *, members: tuple[str, ...] = ("junit.xml",)) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in members:
            archive.writestr(member, document)
    return target.getvalue()


def report(identifier: int, *, skipped: bool = False) -> bytes:
    _, _, conclusion, test = RESULTS[identifier]
    result = '<failure message="' + "x" * 750 + '"/>' if conclusion == "failure" else ""
    if skipped:
        result = "<skipped/>"
    return zipped(
        f'<testsuites><testsuite tests="1" failures="{int(conclusion == "failure")}" '
        f'skipped="{int(skipped)}"><testcase classname="module" name="{test}" '
        f'file="tests/{test}.py">{result}<system-out>{PRIVATE_MARKER}</system-out>'
        "</testcase></testsuite></testsuites>"
    )


class ArchiveServer:
    def __init__(self) -> None:
        self.origin = ""
        self.downloads: list[str] = []
        self.bodies: dict[int, bytes] = {}
        self.fault = ""

    async def respond(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
            start, *headers = request.strip().split("\r\n")
            request_headers = httpx.Headers(
                {name: value for name, value in (line.split(": ", 1) for line in headers)}
            )
            assert request_headers.get("Accept-Encoding") == "identity"
            assert "Authorization" not in request_headers
            assert "Cookie" not in request_headers
            assert "X-Provider-Credential" not in request_headers
            method, target, _ = start.split(" ")
            assert method == "GET"
            self.downloads.append(target)
            path = httpx.URL(target).path
            identifier = int(path.removeprefix("/blob/"))
            body = self.bodies[identifier]
            if self.fault == "redirect":
                writer.write(b"HTTP/1.1 302 Found\r\nLocation: /should-not-follow\r\n\r\n")
            else:
                if self.fault == "oversized_body":
                    body += b"x"
                elif self.fault == "truncated_body":
                    body = body[:-1]
                extra = "Content-Encoding: gzip\r\n" if self.fault == "encoded" else ""
                writer.write(
                    f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n"
                    f"{extra}Set-Cookie: blob=discard\r\nConnection: close\r\n\r\n".encode()
                    + body
                )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.fixture
async def blob() -> AsyncIterator[ArchiveServer]:
    fixture = ArchiveServer()
    async with await asyncio.start_server(fixture.respond, "127.0.0.1", 0) as server:
        fixture.origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        yield fixture


class JUnitFixture(FlakeFixture):
    def __init__(self, blob: ArchiveServer, fault: str = "", *, single_check: bool = False) -> None:
        super().__init__()
        self.blob = blob
        self.junit_fault = fault
        self.single_check = single_check
        self.lookups: list[str] = []
        self.blob.bodies.update(
            {
                identifier: report(
                    identifier,
                    skipped=(fault, identifier)
                    in {("skipped_success", 105), ("skipped_same_head", 102)},
                )
                for identifier in RESULTS
            }
        )

    def run(self, sha: str) -> dict[str, Any]:
        run = RUNS[sha]
        identity = {"id": 70, "full_name": REPO}
        return {
            **status(run, "ci", "success" if sha == MAIN_SHA else "failure"),
            "check_suite_id": run if self.junit_fault != "wrong_suite" else 400,
            "workflow_id": 40,
            "head_sha": sha if self.junit_fault != "wrong_head" else "0" * 40,
            "head_branch": "worker" if sha == FIRST else "trunk",
            "repository": identity,
            "head_repository": (
                {"id": 71, "full_name": "contributor/fork"}
                if self.junit_fault == "fork" and sha == FIRST
                else identity
            ),
            "event": "push",
            "run_attempt": (
                6
                if self.junit_fault == "attempt_limit"
                else 2
                if sha == FIRST and (self.step or self.junit_fault == "ambiguous_attempt")
                else 1
            ),
            "created_at": "2026-11-22T12:00:02Z" if sha == MAIN_SHA else "2026-11-22T12:00:00Z",
        }

    def job_row(self, identifier: int) -> dict[str, Any]:
        sha, name, conclusion, _ = RESULTS[identifier]
        return {
            **status(
                identifier + 900,
                name,
                conclusion if self.junit_fault != "wrong_outcome" else "success",
            ),
            "check_run_url": f"{self.blob.origin}/repos/{REPO}/check-runs/{identifier}",
            "run_id": RUNS[sha],
            "head_sha": sha,
        }

    def artifact(self, identifier: int, name: str) -> dict[str, Any]:
        sha, _, _, _ = RESULTS[identifier]
        body = self.blob.bodies[identifier]
        return {
            "id": identifier,
            "name": name if self.junit_fault != "wrong_name" else f"{name}-other",
            "size_in_bytes": len(body) if self.junit_fault != "size_limit" else MAX_ARCHIVE + 1,
            "expired": self.junit_fault == "expired",
            "digest": "sha256:"
            + (
                "0" * 64
                if self.junit_fault == "wrong_digest"
                or (self.junit_fault == "partial_pass_unavailable" and identifier == 105)
                else hashlib.sha256(body).hexdigest()
            ),
            "archive_download_url": "https://unused.invalid/signed-location",
            "workflow_run": {
                "id": RUNS[sha] if self.junit_fault != "wrong_artifact_run" else 999,
                "repository_id": 70,
                "head_repository_id": 70 if self.junit_fault != "wrong_artifact_repo" else 71,
                "head_sha": sha,
            },
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.copy_with(path="", query=None) == httpx.URL(self.blob.origin)
        assert request.headers["authorization"] == "Bearer fixture"
        assert "blob=" not in request.headers.get("cookie", "")
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        if path.endswith("/check-runs"):
            sha = path.split("/")[2]
            ids = {
                FIRST: [102 if self.step else 101, 103],
                FAILED_MAIN: [104],
                MAIN_SHA: [105, 106],
            }[sha]
            if self.single_check:
                ids = ids[:1]
            checks: list[dict[str, Any]] = []
            for identifier in ids:
                _, name, conclusion, _ = RESULTS[identifier]
                with_annotations = (identifier, self.junit_fault) in {
                    (101, "annotations"),
                    (101, "generic_annotation"),
                    (105, "partial_pass"),
                    (105, "partial_pass_unavailable"),
                }
                check_row = check(identifier, name, conclusion, sha, int(with_annotations))
                check_row["check_suite"]["id"] = RUNS[sha]
                if self.junit_fault != "legacy":
                    check_row["app"]["slug"] = "github-actions"
                checks.append(check_row)
            return httpx.Response(200, json={"check_runs": checks, "total_count": len(checks)})
        if path == "/actions/runs":
            suite = request.url.params.get("check_suite_id")
            if not suite:
                return httpx.Response(
                    200, json={"workflow_runs": [self.run(MAIN_SHA), self.run(FAILED_MAIN)]}
                )
            self.lookups.append(f"suite/{suite}")
            runs = [self.run(sha) for sha, run in RUNS.items() if str(run) == suite]
            if self.junit_fault == "rate_limit":
                return httpx.Response(429, headers={"Retry-After": "5"})
            if self.junit_fault == "ambiguous_run":
                runs *= 2
            return httpx.Response(200, json={"workflow_runs": runs, "total_count": len(runs)})
        if path.endswith("/jobs"):
            self.lookups.append(path)
            run, attempt = (int(path.split("/")[index]) for index in (3, 5))
            ids = {
                (300, 1): [101, 103],
                (300, 2): [101 if self.junit_fault == "ambiguous_attempt" else 102],
                (200, 1): [104],
                (201, 1): [105, 106],
            }[run, attempt]
            jobs = [self.job_row(identifier) for identifier in ids]
            if self.junit_fault == "duplicate_name":
                jobs[1]["name"] = jobs[0]["name"]
            if self.junit_fault == "wrong_attempt":
                jobs[0]["run_attempt"] = attempt + 1
            return httpx.Response(
                200,
                json={
                    "jobs": jobs,
                    "total_count": len(jobs) + int(self.junit_fault == "truncated_jobs"),
                },
            )
        if path.endswith("/artifacts"):
            self.lookups.append(path)
            run = int(path.split("/")[3])
            offered: dict[str, int] = {}
            for identifier, (sha, display_name, _, _) in RESULTS.items():
                if RUNS[sha] == run:
                    attempt = 2 if identifier == 102 else 1
                    name_hash = hashlib.sha256(display_name.encode()).hexdigest()
                    offered[f"plotroom-junit-{run}-{attempt}-{name_hash}"] = identifier
            name = request.url.params["name"]
            assert name in offered
            artifacts = (
                [] if self.junit_fault == "missing" else [self.artifact(offered[name], name)]
            )
            if self.junit_fault == "duplicate_artifact":
                artifacts *= 2
            return httpx.Response(200, json={"artifacts": artifacts, "total_count": len(artifacts)})
        if path.endswith("/zip"):
            identifier = int(path.split("/")[3])
            location = (
                "http://127.0.0.1:1/unsafe"
                if self.junit_fault == "unsafe_redirect"
                else f"{self.blob.origin}/blob/{identifier}?sig={PRIVATE_MARKER}"
            )
            return httpx.Response(
                302, headers={"Location": location, "Set-Cookie": "api=secret-fixture; Path=/"}
            )
        if path == "/check-runs/101/annotations":
            title = "" if self.junit_fault == "generic_annotation" else "module::test_same"
            return httpx.Response(200, json=[annotation("tests/test_same.py", title)])
        if path == "/check-runs/105/annotations":
            return httpx.Response(200, json=[passed_test("tests/test_other.py", "test_other")])
        if path == "/pulls/4/files":
            return httpx.Response(
                200, json=[{"filename": "tests/test_same.py", "status": "modified"}]
            )
        return super().handle(
            httpx.Request(
                request.method,
                request.url.copy_with(scheme="https", host="api.github.com", port=None),
                headers=request.headers,
                content=request.content,
            )
        )


@pytest.mark.parametrize(
    "fault", ["", "skipped_success", "skipped_same_head", "fork", "partial_pass"]
)
async def test_poll_junit_patterns_survive_restart(
    tmp_path: Path, blob: ArchiveServer, caplog: pytest.LogCaptureFixture, fault: str
) -> None:
    fixture = JUnitFixture(blob, fault)
    clock = FixedClock()
    credential = LocalGitHubCredential("TOKEN", {"TOKEN": "fixture"}, None)
    api = GitHubReadClient(blob.origin, REPO)
    caplog.set_level(logging.INFO, "httpx")
    expected: dict[int, tuple[str, str | None]] = {
        101: ("same_head_pass", "module::test_same"),
        103: ("pr_unchanged_test", "module::test_outside"),
        104: ("main_unchanged_test_pass", "module::test_main"),
    }
    if fault == "fork":
        expected[101] = ("same_head_pass", None)
        del expected[103]
    elif fault == "skipped_same_head":
        del expected[101]
    elif fault == "skipped_success":
        del expected[104]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(fixture.handle),
        cookies={"provider": "secret-fixture"},
        headers={"X-Provider-Credential": "secret-fixture"},
    ) as client:
        for restart in (False, True):
            store = SqliteStore("demo", tmp_path)
            await store.migrate()
            ledger = Ledger(
                store, build_catalog(github_repository=REPO), clock, github_repository=REPO
            )
            ledger.start()
            try:
                puller = GitHubPoller(
                    ledger, clock, REPO, credential, PullRequestFetcher(blob.origin, REPO), 1
                )
                details = GitHubDetailPoller(ledger, clock, api, credential)
                if not restart:
                    await puller.poll(client)
                    await details.poll(client)
                    fixture.step = 1
                await details.poll(client)
                if fault == "partial_pass":
                    main_checks = await ledger.github_resource(REPO, CHECKS, MAIN_SHA)
                    assert main_checks is not None
                    recovered = next(check for check in main_checks.records if check.key == "105")
                    assert recovered.details["passed_tests"] == [
                        {"path": "tests/test_other.py", "test_id": "test_other"},
                        {"path": "tests/test_main.py", "test_id": "module::test_main"},
                    ]
                _, items = await ledger.read_next("clerk", 0, 100)
                flakes = [item for item in items if item.kind == "flake_candidate"]
                assert len(flakes) == (0 if restart else len(expected))
                for item in flakes:
                    await ledger.execute("clerk", "ack", item.id, {"item": item.id})
                events = await store.events_after(0)
            finally:
                await ledger.stop()
                await store.close()
    evidence = [event for event in events if event.kind == FLAKE_EVENT]
    assert len(evidence) == len(expected)
    assert {
        event.fields["check_id"]: (event.fields["pattern"], event.fields.get("test_id"))
        for event in evidence
    } == expected
    assert all(event.body == ("x" * 500 if "test_id" in event.fields else "") for event in evidence)
    assert sum(event.kind == MAIN_FLAKE_EVENT for event in events) == int(104 in expected)
    identifiers = (104, 105) if fault == "fork" else (101, 102, 103, 104, 105)
    assert sorted(httpx.URL(target).path for target in blob.downloads) == [
        f"/blob/{identifier}" for identifier in identifiers
    ]
    assert ("/actions/runs/300/artifacts" in fixture.lookups) == (fault != "fork")
    assert PRIVATE_MARKER not in caplog.text
    assert PRIVATE_MARKER.encode() not in await asyncio.to_thread((tmp_path / "demo.db").read_bytes)


@pytest.mark.parametrize(
    "fault",
    [
        "ambiguous_run",
        "wrong_suite",
        "wrong_head",
        "fork",
        "ambiguous_attempt",
        "attempt_limit",
        "duplicate_name",
        "wrong_attempt",
        "wrong_outcome",
        "truncated_jobs",
        "duplicate_artifact",
        "wrong_name",
        "expired",
        "size_limit",
        "wrong_artifact_run",
        "wrong_artifact_repo",
        "wrong_digest",
        "unsafe_redirect",
        "missing",
        "legacy",
        "annotations",
        "redirect",
        "oversized_body",
        "truncated_body",
        "encoded",
    ],
)
async def test_only_known_absence_or_annotations_preserve_fallback(
    blob: ArchiveServer, fault: str
) -> None:
    fixture = JUnitFixture(blob, fault, single_check=True)
    blob.fault = fault
    api = GitHubReadClient(blob.origin, REPO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        result = await fetch_checks(api, client, "fixture", FIRST)
    details = result.records[0].details
    if fault in {"missing", "legacy", "fork"}:
        assert details["annotations"] == []
    elif fault == "annotations":
        assert details["annotations"][0]["test_id"] == "module::test_same"
    else:
        assert "annotations" not in details
    assert "junit_evidence" not in details
    if fault in {"annotations", "legacy"}:
        assert not fixture.lookups
    if fault == "fork":
        assert fixture.lookups == ["suite/300"]
    if fault in {"wrong_digest", "redirect", "oversized_body", "truncated_body", "encoded"}:
        assert len(blob.downloads) == 1
    else:
        assert not blob.downloads


async def test_gate_only_checks_skip_junit_and_provider_limit_propagates(
    blob: ArchiveServer,
) -> None:
    fixture = JUnitFixture(blob, "rate_limit", single_check=True)
    api = GitHubReadClient(blob.origin, REPO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        result = await fetch_checks(api, client, "fixture", FIRST, include_junit=False)
        assert result.records[0].details["annotations"] == []
        assert not fixture.lookups
        with pytest.raises(GitHubRateLimited, match="GitHub rate limit"):
            await fetch_checks(api, client, "fixture", FIRST)
    assert not blob.downloads


async def test_junit_does_not_replace_a_later_structured_annotation(blob: ArchiveServer) -> None:
    fixture = JUnitFixture(blob, "generic_annotation", single_check=True)
    api = GitHubReadClient(blob.origin, REPO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        first = await fetch_checks(api, client, "fixture", FIRST)
        details = first.records[0].details
        assert details["junit_evidence"] is True
        assert details["annotations"][0]["test_id"] == "module::test_same"
        fixture.junit_fault = "annotations"
        observed = await fetch_checks(api, client, "fixture", FIRST, first)
        details = observed.records[0].details
        assert "junit_evidence" not in details
        assert details["annotation_count"] == 1
        assert details["annotations"][0]["test_id"] == "module::test_same"
    assert len(blob.downloads) == 1


async def test_verified_misses_expire_and_malformed_report_projects_unknown(
    blob: ArchiveServer,
) -> None:
    fixture = JUnitFixture(blob, "missing", single_check=True)
    api = GitHubReadClient(blob.origin, REPO)
    valid = blob.bodies[101]
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        absent = await fetch_checks(api, client, "fixture", FIRST)
        assert absent.records[0].details["annotations"] == []
        looked_up = fixture.lookups.copy()
        fixture.junit_fault = ""
        blob.bodies[101] = zipped("<testsuite>")
        for _ in range(JUNIT_MISS_REFRESHES):
            absent = await fetch_checks(api, client, "fixture", FIRST, absent)
            assert absent.records[0].details["annotations"] == []
            assert fixture.lookups == looked_up
        invalid = await fetch_checks(api, client, "fixture", FIRST, absent)
        assert "annotations" not in invalid.records[0].details
        blob.bodies[101] = valid
        for _ in range(JUNIT_UNAVAILABLE_REFRESHES):
            invalid = await fetch_checks(api, client, "fixture", FIRST, invalid)
            assert "annotations" not in invalid.records[0].details
            assert len(blob.downloads) == 1
        available = await fetch_checks(api, client, "fixture", FIRST, invalid)
        assert available.records[0].details["junit_evidence"] is True
        assert available.records[0].details["annotations"][0]["test_id"] == "module::test_same"
    assert len(blob.downloads) == 2


async def test_green_check_without_relevant_failures_skips_junit(blob: ArchiveServer) -> None:
    fixture = JUnitFixture(blob, single_check=True)
    fixture.step = 1
    api = GitHubReadClient(blob.origin, REPO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        result = await fetch_checks(api, client, "fixture", FIRST)
    assert result.records[0].details["passed_tests"] == []
    assert not fixture.lookups
    assert not blob.downloads


async def test_unavailable_junit_preserves_independent_pass_annotations(
    blob: ArchiveServer,
) -> None:
    fixture = JUnitFixture(blob, "partial_pass_unavailable", single_check=True)
    api = GitHubReadClient(blob.origin, REPO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        observed = await fetch_checks(api, client, "fixture", MAIN_SHA, recovery_check_ids=(105,))
    assert observed.records[0].details["passed_tests"] == [
        {"path": "tests/test_other.py", "test_id": "test_other"},
    ]
    assert "junit_evidence" not in observed.records[0].details
    assert observed.records[0].details["junit_state"] == "unavailable"
    assert len(blob.downloads) == 1


@pytest.mark.parametrize("partial_metadata", [False, True])
async def test_truncated_delayed_annotations_cannot_reuse_stale_success_evidence(
    blob: ArchiveServer, partial_metadata: bool
) -> None:
    fixture = JUnitFixture(blob, "annotations", single_check=True)
    api = GitHubReadClient(blob.origin, REPO)
    shortened = False
    annotation_reads = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal annotation_reads
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        if path == "/check-runs/102/annotations":
            annotation_reads += 1
            return httpx.Response(
                200, json=[passed_test("tests/test_same.py", "module::test_same")]
            )
        response = fixture.handle(request)
        if shortened and path == f"/commits/{FIRST}/check-runs":
            payload: dict[str, Any] = response.json()
            row: dict[str, Any] = payload["check_runs"][0]
            row["output"]["annotations_count"] = 2
            if partial_metadata:
                del row["app"], row["check_suite"], row["head_sha"]
            return httpx.Response(200, json=payload)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        failed = await fetch_checks(api, client, "fixture", FIRST)
        fixture.step = 1
        verified = await fetch_checks(api, client, "fixture", FIRST, failed)
        assert verified.records[0].details["junit_evidence"] is True
        shortened = True
        for _ in range(2):
            verified = await fetch_checks(api, client, "fixture", FIRST, verified)
            success = verified.records[0]
            assert success.details["junit_state"] == "unavailable"
            assert "passed_tests" not in success.details
            assert "junit_evidence" not in success.details
            if partial_metadata:
                assert "app_id" not in success.details
                assert "suite_id" not in success.details
                assert success.details["junit_prior_identity"] == {
                    "app_id": 8,
                    "suite_id": RUNS[FIRST],
                }

    assert annotation_reads == 2
    assert len(blob.downloads) == 1
    success = verified.records[0]
    (failure,) = prior_check_failures(verified)
    runs = tuple(
        GitHubRecord(
            recorded.key,
            "workflow_run",
            {
                "head_sha": FIRST,
                "jobs": [{**recorded.fields, "check_run_id": recorded.fields["id"]}],
            },
            details={"workflow_id": 40},
        )
        for recorded in (success, failure)
    )
    uow = create_autospec(UnitOfWork, instance=True)
    uow.find_event.return_value = None
    await record_same_head(uow, REPO, verified)
    (pair,) = main_pairs(runs, {FIRST: verified})
    await record_main_flakes(uow, REPO, pair, None)
    uow.append.assert_not_awaited()


async def test_shared_junit_budget_defers_other_heads_until_next_poll(blob: ArchiveServer) -> None:
    fixture = JUnitFixture(blob, single_check=True)
    api = GitHubReadClient(blob.origin, REPO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        budget = JUnitBudget(attempts_left=1)
        read = await fetch_checks(api, client, "fixture", FIRST, junit_budget=budget)
        deferred = await fetch_checks(api, client, "fixture", FAILED_MAIN, junit_budget=budget)
        assert read.records[0].details["junit_evidence"] is True
        assert deferred.complete
        assert "annotations" not in deferred.records[0].details
        assert deferred.records[0].details["junit_state"] == "deferred"
        assert len(blob.downloads) == 1
        assert "suite/200" not in fixture.lookups

        budget = JUnitBudget(attempts_left=1)
        cached = await fetch_checks(api, client, "fixture", FIRST, read, junit_budget=budget)
        assert cached.records[0].details["junit_evidence"] is True
        assert budget.attempts_left == 1
        resumed = await fetch_checks(
            api, client, "fixture", FAILED_MAIN, deferred, junit_budget=budget
        )
        assert resumed.records[0].details["junit_evidence"] is True
    assert [httpx.URL(target).path for target in blob.downloads] == ["/blob/101", "/blob/104"]


async def test_detail_poll_rotates_optional_work_past_expiring_misses(
    tmp_path: Path, blob: ArchiveServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = JUnitFixture(blob)
    extra_ids = tuple(range(110, 115))
    refreshed: list[str] = []
    attempted: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        response = fixture.handle(request)
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        if path.endswith("/check-runs"):
            sha = path.split("/")[2]
            refreshed.append(sha)
            if sha == FIRST:
                original: list[dict[str, Any]] = response.json()["check_runs"]
                for identifier in extra_ids:
                    check_row = check(identifier, f"job-{identifier}", "failure", sha, 0)
                    check_row["app"]["slug"] = "github-actions"
                    check_row["check_suite"]["id"] = RUNS[sha]
                    original.append(check_row)
                return httpx.Response(
                    200, json={"check_runs": original, "total_count": len(original)}
                )
        return response

    async def absent(
        _reader: JUnitReader, fields: dict[str, Any], _suite: int, *, passed: bool
    ) -> None:
        attempted.append(fields["id"])

    monkeypatch.setattr(JUnitReader, "evidence", absent)
    monkeypatch.setattr(details_adapter, "JUnitBudget", lambda: JUnitBudget(attempts_left=1))
    store = SqliteStore("demo", tmp_path)
    await store.migrate()
    clock = FixedClock()
    ledger = Ledger(store, build_catalog(github_repository=REPO), clock, github_repository=REPO)
    credential = LocalGitHubCredential("TOKEN", {"TOKEN": "fixture"}, None)
    puller = GitHubPoller(ledger, clock, REPO, credential, PullRequestFetcher(blob.origin, REPO), 1)
    details = GitHubDetailPoller(ledger, clock, GitHubReadClient(blob.origin, REPO), credential)
    ledger.start()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await puller.poll(client)
            for _ in range(3 * (JUNIT_MISS_REFRESHES + 1)):
                refreshed.clear()
                prior_attempts = len(attempted)
                await details.poll(client)
                assert refreshed == [MAIN_SHA, FAILED_MAIN, FIRST]
                assert len(attempted) - prior_attempts <= 1
    finally:
        await ledger.stop()
        await store.close()
    assert attempted[:3] == [105, 104, 101]
    assert set(attempted) == {105, 104, 101, 103, *extra_ids}


async def test_budget_retries_last_check_after_its_initial_miss(
    blob: ArchiveServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = JUnitFixture(blob, single_check=True)
    identifiers = tuple(range(110, 110 + 2 * (JUNIT_MISS_REFRESHES + 1)))
    last = identifiers[-1]
    attempts: list[int] = []
    uploaded = False

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != f"/repos/{REPO}/commits/{FIRST}/check-runs":
            return fixture.handle(request)
        checks: list[dict[str, Any]] = []
        for identifier in identifiers:
            row = check(identifier, f"job-{identifier}", "failure", FIRST, 0)
            row["app"]["slug"] = "github-actions"
            row["check_suite"]["id"] = RUNS[FIRST]
            checks.append(row)
        return httpx.Response(200, json={"check_runs": checks, "total_count": len(checks)})

    async def evidence(
        _reader: JUnitReader, fields: dict[str, Any], _suite: int, *, passed: bool
    ) -> list[dict[str, str]] | None:
        identifier = fields["id"]
        attempts.append(identifier)
        if identifier == last and uploaded:
            return [{"path": "test.py", "test_id": "module::test_late", "excerpt": "failed"}]
        return None

    monkeypatch.setattr(JUnitReader, "evidence", evidence)
    previous: GitHubResource | None = None
    api = GitHubReadClient(blob.origin, REPO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        for _ in range(3 * len(identifiers)):
            previous = await fetch_checks(
                api, client, "fixture", FIRST, previous, junit_budget=JUnitBudget(attempts_left=1)
            )
            latest = previous.records[-1].details
            if latest.get("junit_evidence") is True:
                assert latest["annotations"][0]["test_id"] == "module::test_late"
                break
            if latest.get("junit_miss_remaining") == JUNIT_MISS_REFRESHES:
                assert latest["annotations"] == []
                uploaded = True
        else:
            pytest.fail("The delayed report never received a retry slot")
    assert attempts[: len(identifiers)] == list(identifiers)
    assert attempts.count(last) == 2


TESTCASE = '<testcase classname="module" name="test" file="tests/test.py"/>'


@pytest.mark.parametrize(
    "archive",
    [
        b"x",
        zipped("<testsuite>"),
        zipped(f"<testsuite tests='2'>{TESTCASE}</testsuite>"),
        zipped(f"<testsuite>{TESTCASE}{TESTCASE}</testsuite>"),
        zipped(f"<testsuite>{TESTCASE.replace('tests/', '../')}</testsuite>"),
        zipped(f"<testsuite>{TESTCASE.replace('tests/', 'C:/')}</testsuite>"),
        zipped(
            f"<testsuite>{TESTCASE.replace('tests/', '')}</testsuite>", members=("../junit.xml",)
        ),
        zipped(f"<testsuite>{TESTCASE}</testsuite>", members=("junit.xml", "other.xml")),
        zipped(f"<testsuite>{TESTCASE.replace('/>', " status='notrun'/>")}</testsuite>"),
        zipped('<!DOCTYPE testsuite [<!ENTITY x "data">]><testsuite>&x;</testsuite>'),
        zipped('<!DOCTYPE testsuite SYSTEM "file:///unreadable"><testsuite/>'.encode("utf-16")),
        zipped("<testsuite>" + " " * MAX_XML + "</testsuite>"),
        zipped("<testsuite>" * 40 + "</testsuite>" * 40),
    ],
)
def test_invalid_junit_never_becomes_an_empty_report(archive: bytes) -> None:
    with pytest.raises(ValueError, match=r"JUnit|UTF-8|utf-8"):
        junit_evidence(archive, passed=True)


@pytest.mark.parametrize(
    "url",
    [
        "https://results-receiver.actions.githubusercontent.com.evil.invalid/blob",
        "https://results-receiver.actions.githubusercontent.com:444/blob",
        "https://results-receiver.actions.githubusercontent.com@outside.invalid/blob",
        "https://user@results-receiver.actions.githubusercontent.com/blob",
        "http://test.blob.core.windows.net/blob",
        "https://outside.invalid/test.blob.core.windows.net",
        "http://127.0.0.1:9001/blob",
        "//test.blob.core.windows.net/blob",
    ],
)
def test_only_allowed_download_origins(url: str) -> None:
    with pytest.raises(ValueError, match="JUnit download"):
        _download_url(url, "http://127.0.0.1:9000")


@pytest.mark.parametrize(
    "url",
    [
        "https://results-receiver.actions.githubusercontent.com/blob?sig=fixture",
        "https://test.blob.core.windows.net/blob?sig=fixture",
        "http://127.0.0.1:9000/blob?sig=fixture",
    ],
)
def test_accepts_supported_download_origins(url: str) -> None:
    assert str(_download_url(url, "http://127.0.0.1:9000")) == url
