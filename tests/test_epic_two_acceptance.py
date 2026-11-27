import importlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

# Epic 2's fixed synthetic contract, including both transport and ingest variants.
REQUIRED_WITNESSES = (
    "test_github.py::test_pull_lifecycle_and_credentials_preserve_committed_state",
    "test_github.py::test_on_demand_daemon_reads_the_mounted_pat",
    "test_github_details.py::test_github_transitions_and_pagination_are_durable",
    "test_switchboard.py::test_switchboard_redelivery_after_interrupted_ack",
    "test_intake.py::test_worker_ingest_wakes_local_watch_and_ack_closes[None]",
    "test_intake.py::test_worker_ingest_wakes_local_watch_and_ack_closes[2]",
    "test_github_app.py::test_app_daemon_keeps_polling_across_short_installation_tokens",
    "test_work_items_github_tree.py::test_default_branch_tick_matches_checkout_and_preserves_last_good_view",
    "test_github_details.py::test_github_issues_flow_through_the_runtime_tick",
    "test_work_items_github_issues.py::test_work_items_match_the_synced_bmad_board",
    "test_deploy_status.py::test_paginated_get_only_deployments_isolate_failures_and_deduplicate",
    "test_deploy_status.py::test_only_own_lane_worker_can_report_and_replay[http]",
    "test_deploy_status.py::test_only_own_lane_worker_can_report_and_replay[stdio]",
)


@pytest.fixture
def acceptance(monkeypatch: pytest.MonkeyPatch) -> Any:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(sys, "path", [str(root), *sys.path])
    return importlib.import_module("deploy.epic_two_acceptance")


@pytest.mark.parametrize("synthetic", [True, False])
def test_clean_witnesses_only_pass_in_synthetic_mode(
    acceptance: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    synthetic: bool,
) -> None:
    calls: list[str] = []

    def compose(tag: str) -> None:
        calls.append(tag)

    monkeypatch.setattr(acceptance, "local_checks", lambda: calls.append("pytest"))
    monkeypatch.setattr(acceptance, "compose_checks", compose)

    args = ["--image-tag", "fixture"]
    if synthetic:
        args.append("--synthetic")
    assert acceptance.main(args) == (0 if synthetic else 1)
    assert calls == ["pytest", "fixture"]
    output = capsys.readouterr().out
    assert [line[0] for line in output.splitlines() if line[:1].isdigit()] == list("123456789")
    assert output.count("Local synthetic PASS") == 9
    assert output.count("Compose synthetic PASS") == 5
    assert output.count("Full BLOCKED") == 6
    assert "CI fixture PAT" in output
    assert "approved CI App credential" in output
    assert "Existing recorded GitHub deployment" in output
    assert "Epic 2 Notes" in output
    assert "Integrated credential-backed CI acceptance remains pending" in output
    summary = "remains incomplete" if synthetic else "full acceptance blocked"
    assert summary in output


@pytest.mark.parametrize("failed", ["local_checks", "compose_checks"])
def test_failed_environment_stays_unverified(
    acceptance: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed: str,
) -> None:
    def fail(*_args: str) -> None:
        raise subprocess.CalledProcessError(1, "fixture-check")

    def compose(_tag: str) -> None:
        pass

    monkeypatch.setattr(acceptance, "local_checks", lambda: None)
    monkeypatch.setattr(acceptance, "compose_checks", compose)
    monkeypatch.setattr(acceptance, failed, fail)
    assert acceptance.main(["--image-tag", "fixture", "--synthetic"]) == 1
    captured = capsys.readouterr()
    environment = "Local" if failed == "local_checks" else "Compose"
    assert environment in captured.err
    assert "FAILED (CalledProcessError)" in captured.err
    assert f"{environment} synthetic UNVERIFIED" in captured.out
    assert f"{environment} synthetic PASS" not in captured.out
    assert "Full BLOCKED" in captured.out


@pytest.mark.parametrize("fault", [None, "skipped", "wrong-identity"])
def test_local_checks_require_every_exact_witness(
    acceptance: Any, monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    def pytest_run(command: list[str], **_kwargs: object) -> None:
        assert [arg for arg in command if arg.startswith("tests/")] == [
            f"tests/{selector}" for selector in REQUIRED_WITNESSES
        ]
        report = Path(
            next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml="))
        )
        cases: list[str] = []
        for selector in REQUIRED_WITNESSES:
            filename, name = selector.split("::")
            status = ""
            if selector == REQUIRED_WITNESSES[-1]:
                if fault == "wrong-identity":
                    name = "unrelated_passing_test"
                elif fault == "skipped":
                    status = "<skipped />"
            cases.append(
                f'<testcase classname="tests.{Path(filename).stem}" name="{name}">'
                f"{status}</testcase>"
            )
        report.write_text(
            "<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>",
            encoding="utf-8",
        )

    monkeypatch.setattr(acceptance.subprocess, "run", pytest_run)
    if fault is None:
        acceptance.local_checks()
        return
    with pytest.raises(RuntimeError, match="local witnesses did not run to completion"):
        acceptance.local_checks()


def test_spike_finding_must_be_in_epic_notes(
    acceptance: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    epic = tmp_path / "epic.md"
    epic.write_text(
        "## Notes\n\n## Other\n- Finding (entry 1, #31, date): read-only SessionReader.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(acceptance, "EPIC_NOTES", epic)
    with pytest.raises(RuntimeError, match="spike answer is missing"):
        acceptance.local_checks()


@pytest.mark.parametrize("available", [True, False])
def test_archived_rows_are_explicit_and_full_mode_remains_blocked(
    acceptance: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    available: bool,
) -> None:
    calls: list[str] = []

    async def archived(tag: str, _report: Any) -> None:
        calls.append(tag)
        if not available:
            raise RuntimeError("the read-only fixture input is missing")

    def compose(_tag: str) -> None:
        pass

    monkeypatch.setattr(acceptance, "local_checks", lambda: None)
    monkeypatch.setattr(acceptance, "compose_checks", compose)
    monkeypatch.setattr(acceptance, "archived_checks", archived)
    assert acceptance.main(["--image-tag", "fixture", "--archived-github"]) == 1

    captured = capsys.readouterr()
    assert calls == ["fixture"]
    criterion = 0
    archived_rows: set[int] = set()
    pending_rows: set[int] = set()
    blocked_rows: set[int] = set()
    for line in captured.out.splitlines():
        if line[:1].isdigit():
            criterion = int(line.split(".", 1)[0])
        elif "Archived GitHub PASS" in line:
            archived_rows.add(criterion)
        elif "Full PENDING" in line:
            pending_rows.add(criterion)
        elif "Full BLOCKED" in line:
            blocked_rows.add(criterion)
    assert archived_rows == ({1, 9} if available else set())
    assert pending_rows == archived_rows
    assert blocked_rows == ({2, 5, 6, 7} if available else {1, 2, 5, 6, 7, 9})
    if available:
        assert "Original live transitions remain outside" in captured.out
        assert "full acceptance blocked" in captured.out
    else:
        assert "read-only fixture input is missing" in captured.err
        assert "archived GitHub checks failed" in captured.out


def test_archive_broker_allows_only_fixture_reads_and_replays_only_captured_inputs(
    acceptance: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = importlib.import_module(acceptance.archived_checks.__module__)
    provider: Any = archive.ProviderReads()
    calls: list[list[str]] = []

    def read(command: list[str], body: bytes) -> tuple[bytes, int]:
        calls.append(command)
        assert body == b""
        return (
            b"HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n"
            b"Authorization: unsafe-header\r\nRetry-After: 1\r\n\r\n[]",
            0,
        )

    monkeypatch.setattr(archive, "_read_gh", read)
    endpoint = "/repos/mgd43b/plotroom-test/pulls?state=all"
    expected = ({"retry-after": "1"}, b"[]")
    assert provider.read("GET", endpoint, b"") == expected
    assert provider.replay("GET", endpoint, b"") == expected
    assert len(calls) == 1
    assert calls[0][0:8] == [
        "gh",
        "api",
        "--include",
        "--hostname",
        "github.com",
        "--method",
        "GET",
        "-H",
    ]

    with pytest.raises(RuntimeError, match="absent from the local capture"):
        provider.replay("GET", f"{endpoint}&page=2", b"")
    for method, path, body in (
        ("PUT", endpoint, b""),
        ("GET", "/repos/mgd43b/plotroom/pulls", b""),
        ("GET", "/repos/mgd43b/plotroom-test/actions/runs/1/logs", b""),
        ("POST", "/graphql", b'{"query":"mutation { changed }","variables":{}}'),
    ):
        with pytest.raises(ValueError, match="outside"):
            provider.read(method, path, body)
    assert len(calls) == 1


def test_archive_graphql_query_is_bound_to_the_fixture(acceptance: Any) -> None:
    archive: Any = importlib.import_module(acceptance.archived_checks.__module__)
    scoped: dict[str, object] = {
        "owner": "mgd43b",
        "name": "plotroom-test",
        "number": 2,
        "after": None,
    }

    def document(variables: dict[str, object]) -> bytes:
        return json.dumps({"query": archive.REVIEW_THREADS_QUERY, "variables": variables}).encode()

    assert archive._scoped_endpoint("POST", "/graphql", document(scoped)) == "graphql"
    for key, value in (("owner", "other"), ("name", "plotroom"), ("number", 3)):
        with pytest.raises(ValueError, match="outside"):
            archive._scoped_endpoint("POST", "/graphql", document({**scoped, key: value}))


def test_archive_preflight_stops_before_details_if_fixture_is_private(
    acceptance: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive: Any = importlib.import_module(acceptance.archived_checks.__module__)
    calls: list[str] = []

    def read(command: list[str], body: bytes) -> tuple[bytes, int]:
        assert not body
        calls.append(command[-1])
        return (
            b"HTTP/2.0 200 OK\r\n\r\n"
            b'{"full_name":"mgd43b/plotroom-test","visibility":"private","private":true}',
            0,
        )

    monkeypatch.setattr(archive, "_read_gh", read)
    with pytest.raises(RuntimeError, match="fixture is not public"):
        archive.ProviderReads().preflight()
    assert calls == ["repos/mgd43b/plotroom-test"]


def test_archive_capture_rejects_oversize_child_stdout(
    acceptance: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive: Any = importlib.import_module(acceptance.archived_checks.__module__)
    monkeypatch.setattr(archive, "MAX_RESPONSE", 16)
    with pytest.raises(RuntimeError, match="HTTP response exceeded the byte bound"):
        archive._read_gh([sys.executable, "-c", "print('x' * 70000)"], b"")


async def test_archive_loopback_requires_the_disposable_marker(acceptance: Any) -> None:
    archive: Any = importlib.import_module(acceptance.archived_checks.__module__)
    calls: list[str] = []

    def read(_method: str, path: str, _body: bytes) -> tuple[dict[str, str], bytes]:
        calls.append(path)
        return {}, b"[]"

    async with archive.loopback(read, "disposable-test-marker") as value:
        endpoint: str = value[0]
        async with httpx.AsyncClient(trust_env=False) as client:
            denied = await client.get(endpoint + "/fixture")
            assert denied.status_code == 403
            assert calls == []
            wrong_marker = await client.get(
                endpoint + "/fixture", headers={"Authorization": "Bearer wrong-test-marker"}
            )
            assert wrong_marker.status_code == 403
            assert calls == []
            accepted = await client.get(
                endpoint + "/fixture", headers={"Authorization": "Bearer disposable-test-marker"}
            )
            assert accepted.status_code == 200
            assert calls == ["/fixture"]


@pytest.mark.parametrize(
    "args",
    [
        ["--milestone", "2"],
        ["--work-item-mirrors", "--milestone", "2"],
        ["--work-item-mirrors", "--milestone", "0", "--child", "4"],
        ["--work-item-mirrors", "--synthetic"],
    ],
)
def test_mirror_fixture_ids_must_be_explicit_and_paired(acceptance: Any, args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        acceptance.main(["--image-tag", "fixture", *args])
    assert error.value.code == 2


@pytest.mark.parametrize("complete", [True, False, None])
def test_row_six_mirror_observations_do_not_pass_full_acceptance(
    acceptance: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    complete: bool | None,
) -> None:
    seen: list[tuple[int | None, int | None]] = []

    async def mirror(
        milestone: int | None, child: int | None, report: Callable[[str], None]
    ) -> bool:
        seen.append((milestone, child))
        report("Repo-store mirror PASS")
        if complete is None:
            raise RuntimeError("fixture milestone unavailable")
        return complete

    def compose(_tag: str) -> None:
        pass

    monkeypatch.setattr(acceptance, "local_checks", lambda: None)
    monkeypatch.setattr(acceptance, "compose_checks", compose)
    monkeypatch.setattr(acceptance, "work_item_mirrors", mirror)
    numbers = ["--milestone", "6", "--child", "25"] if complete else []
    assert acceptance.main(["--image-tag", "fixture", "--work-item-mirrors", *numbers]) == 1
    assert seen == ([(6, 25)] if complete else [(None, None)])
    captured = capsys.readouterr()
    output = captured.out
    row_six = output.split("\n6. ", 1)[1].split("\n7. ", 1)[0]
    assert ("Full PENDING" if complete else "Full BLOCKED") in row_six
    if complete is None:
        assert "fixture milestone unavailable" in captured.err
        assert "work-item mirror checks failed" in output


async def test_repo_store_rejects_moved_main_before_fetching_git_objects(
    acceptance: Any,
) -> None:
    mirror: Any = importlib.import_module(acceptance.work_item_mirrors.__module__)
    prefix = "/repos/mgd43b/plotroom-test"
    calls: list[str] = []

    def reader(method: str, path: str, body: bytes) -> tuple[dict[str, str], bytes]:
        assert method == "GET"
        assert not body
        calls.append(path)
        values = {
            prefix: {"default_branch": "main"},
            prefix + "/branches/main": {
                "commit": {"sha": "b" * 40, "commit": {"tree": {"sha": mirror.ROOT_TREE}}}
            },
        }
        return {}, json.dumps(values[path]).encode()

    with pytest.raises(RuntimeError, match="default-branch tree changed"):
        await mirror.tree_witness(reader)
    assert calls == [prefix, prefix + "/branches/main"]
    with pytest.raises(RuntimeError, match="outside"):
        mirror.tree_scope("GET", prefix + "/git/blobs/" + "b" * 40, b"")


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "inherited",
        "closed",
        "unrefined",
        "body-prose",
        "extra",
        "wrong-body",
        "extra-relation",
        "cross-repo-relation",
        "child-drift",
        "milestone-pagination",
        "relation-pagination",
    ],
)
async def test_native_mirror_reads_only_the_pinned_milestone_and_relationships(
    acceptance: Any, fault: str | None
) -> None:
    mirror: Any = importlib.import_module(acceptance.work_item_mirrors.__module__)
    scope: Any = mirror.IssueScope(6, 25)
    prefix = "/repos/mgd43b/plotroom-test"
    page = f"{prefix}/issues?milestone=6&state=all&per_page=100&page=1"
    parent_path = f"{prefix}/issues/24"
    child_path = f"{prefix}/issues/25"
    suffix = "?per_page=100&page=1"
    calls: list[str] = []
    native_body = "```yaml\r\ntype: story\r\nrefined: true\r\n```\r\n\r\nExact emoji 📌.\r\n"
    if fault == "body-prose":
        native_body = "```yaml\r\ntype: story\r\n```\r\nrefined: true\r\n"
    if fault == "unrefined":
        native_body = native_body.replace("refined: true", "refined: false")

    def entry(number: int, title: str, labels: list[str], body: str) -> dict[str, Any]:
        return {
            "number": number,
            "title": title,
            "labels": [{"name": name} for name in labels],
            "body": body,
            "state": "open",
            "milestone": {"number": 6},
            "assignees": [],
            "updated_at": "2026-11-27T19:00:00Z",
        }

    child = entry(25, "Fixture issue", ["story", "backlog"], native_body)
    parent = entry(24, "Fixture epic", ["epic", "backlog"], "")
    if fault == "inherited":
        child["milestone"] = None
    listed = [parent]
    if fault != "inherited":
        listed.append(
            {**child, "body": native_body + "changed"} if fault == "wrong-body" else child
        )
    if fault == "child-drift":
        child = {**child, "labels": [*child["labels"], {"name": "in-progress"}]}
    if fault == "extra":
        listed.append(entry(26, "Unapproved issue", ["story", "backlog"], native_body))
    related: list[dict[str, Any]] = [
        {"number": 25, "repository_url": f"https://api.github.com{prefix}"}
    ]
    if fault == "extra-relation":
        related.append({"number": 900, "repository_url": f"https://api.github.com{prefix}"})
    if fault == "cross-repo-relation":
        related.append(
            {"number": 901, "repository_url": "https://api.github.com/repos/other/other"}
        )
    values: dict[str, Any] = {
        f"{prefix}/milestones/6": {
            "number": 6,
            "state": "closed" if fault == "closed" else "open",
            "title": "Fixture",
        },
        child_path: child,
        page: listed,
        f"{parent_path}/sub_issues{suffix}": related,
        f"{child_path}/sub_issues{suffix}": [],
        f"{parent_path}/dependencies/blocked_by{suffix}": [],
        f"{child_path}/dependencies/blocked_by{suffix}": [],
    }

    def reader(method: str, path: str, body: bytes) -> tuple[dict[str, str], bytes]:
        assert method == "GET"
        assert not body
        calls.append(path)
        paginated = (fault == "milestone-pagination" and path == page) or (
            fault == "relation-pagination" and path == f"{parent_path}/sub_issues{suffix}"
        )
        headers = {"link": f'<https://api.github.com{path[:-1]}2>; rel="next"'} if paginated else {}
        return headers, json.dumps(values[path]).encode()

    for outside in (
        f"{prefix}/issues",
        f"{prefix}/milestones",
        f"{prefix}/issues/123",
        f"{parent_path}/sub_issues{suffix}",
    ):
        with pytest.raises(RuntimeError, match="outside"):
            scope.endpoint("GET", outside, b"")
    if fault is not None and fault != "inherited":
        with pytest.raises(RuntimeError):
            await mirror.issues_witness(reader, scope, 6, 25)
        if fault in {
            "closed",
            "body-prose",
            "unrefined",
            "extra",
            "child-drift",
            "milestone-pagination",
        }:
            assert not any("sub_issues" in path or "blocked_by" in path for path in calls)
        assert all(
            all(part not in path for part in ("/900", "/901", "/26", "page=2")) for path in calls
        )
        return
    ready = await mirror.issues_witness(reader, scope, 6, 25)
    assert (ready.ref, ready.title) == ("25", "Fixture issue")
    assert set(calls) == set(values)
    assert len(calls) == len(values)
