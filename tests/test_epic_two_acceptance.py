import asyncio
import hashlib
import importlib
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from plotroom.ports.work_items import WorkItem

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


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "local",
        "compose",
        "archive",
        "mirrors",
        "compose-timeout",
        "archive-timeout",
        "mirrors-timeout",
    ],
)
def test_integrated_result_requires_each_included_witness_and_reports_its_scope(
    acceptance: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fault: str | None,
) -> None:
    calls: list[str] = []
    path = tmp_path / "fixture-token"
    failed_stage = fault.removesuffix("-timeout") if fault else None

    def observe(stage: str) -> None:
        calls.append(stage)
        if fault == stage == "archive":
            raise acceptance.ArchiveReadError(
                "GitHub fixture read returned HTTP 403 at GraphQL review threads for PR #2"
            )
        if fault == stage:
            raise RuntimeError("untrusted error detail")

    async def archive(tag: str, token_file: Path) -> None:
        assert tag == "fixture"
        assert token_file == path
        observe("archive")
        if fault == "archive-timeout":
            await asyncio.sleep(0.2)

    async def mirrors(tag: str, token_file: Path, _report: Callable[[str], None]) -> None:
        assert tag == "fixture"
        assert token_file == path
        observe("mirrors")
        if fault == "mirrors-timeout":
            await asyncio.sleep(0.2)

    def compose(tag: str, *, stage_timer: Callable[[bool], None]) -> None:
        assert tag == "fixture"
        try:
            observe("compose")
            if fault == "compose-timeout":
                time.sleep(0.2)
        finally:
            stage_timer(False)

    monkeypatch.setattr(acceptance, "local_checks", lambda: observe("local"))
    monkeypatch.setattr(acceptance, "compose_checks", compose)
    monkeypatch.setattr(acceptance, "archived_file_checks", archive)
    monkeypatch.setattr(acceptance, "verify_published", mirrors)
    monkeypatch.setattr(acceptance, "COMPOSE_STAGE_SECONDS", 0.05)
    monkeypatch.setattr(acceptance, "GITHUB_STAGE_SECONDS", 0.05)
    assert acceptance.main(
        ["--verify-integrated", "--image-tag", "fixture", "--token-file", str(path)]
    ) == (0 if fault is None else 1)
    assert calls == ["local", "compose", "archive", "mirrors"]
    captured = capsys.readouterr()
    row = 0
    archive_rows: set[int] = set()
    live_rows: set[int] = set()
    local_rows: set[int] = set()
    compose_rows: set[int] = set()
    deferred: set[int] = set()
    for line in captured.out.splitlines():
        if line[:1].isdigit():
            row = int(line.split(".", 1)[0])
        elif line.startswith("   Archived GitHub "):
            archive_rows.add(row)
            assert ("UNVERIFIED" if failed_stage == "archive" else "PASS") in line
            assert "terminal facts" in line
        elif line.startswith("   Live read-only "):
            live_rows.add(row)
            assert ("UNVERIFIED" if failed_stage == "mirrors" else "PASS") in line
            assert "capture replay" in line
        elif "External acceptance DEFERRED" in line:
            deferred.add(row)
        elif line.startswith("   Local synthetic "):
            local_rows.add(row)
            assert ("UNVERIFIED" if failed_stage == "local" else "PASS") in line
        elif line.startswith("   Compose synthetic "):
            compose_rows.add(row)
            assert ("UNVERIFIED" if failed_stage == "compose" else "PASS") in line
    assert archive_rows == {1, 9}
    assert live_rows == {6}
    assert deferred == {2, 5, 7}
    assert local_rows == set(range(1, 10))
    assert compose_rows == {2, 3, 4, 8, 9}
    assert "Full #46 acceptance remains pending." in captured.out
    assert "untrusted error detail" not in captured.err
    if fault == "archive":
        assert "HTTP 403 at GraphQL review threads for PR #2" in captured.err
    if fault and fault.endswith("-timeout"):
        assert "FAILED (TimeoutError)" in captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--verify-integrated"],
        ["--token-file", "fixture"],
        ["--verify-integrated", "--synthetic", "--token-file", "fixture"],
        ["--verify-integrated", "--milestone", "1", "--child", "7", "--token-file", "fixture"],
    ],
)
def test_integrated_cli_requires_its_private_file_and_fixed_fixture(
    acceptance: Any, arguments: list[str]
) -> None:
    with pytest.raises(SystemExit) as result:
        acceptance.main(["--image-tag", "fixture", *arguments])
    assert result.value.code == 2


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
        ["--milestone", "2", "--child", "4"],
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
    seen: list[tuple[str, int | None, int | None]] = []

    async def mirror(
        tag: str, milestone: int | None, child: int | None, report: Callable[[str], None]
    ) -> bool:
        seen.append((tag, milestone, child))
        report("Repo-store mirror PASS")
        if complete is None:
            raise RuntimeError("fixture milestone unavailable")
        return complete

    def compose(_tag: str) -> None:
        pass

    monkeypatch.setattr(acceptance, "local_checks", lambda: None)
    monkeypatch.setattr(acceptance, "compose_checks", compose)
    monkeypatch.setattr(acceptance, "work_item_mirrors", mirror)
    numbers = ["--milestone", "6", "--child", "25"] if complete is not False else []
    assert acceptance.main(["--image-tag", "fixture", "--work-item-mirrors", *numbers]) == 1
    assert seen == ([("fixture", 6, 25)] if complete is not False else [("fixture", None, None)])
    captured = capsys.readouterr()
    output = captured.out
    row_six = output.split("\n6. ", 1)[1].split("\n7. ", 1)[0]
    assert ("Full PENDING" if complete else "Full BLOCKED") in row_six
    if complete is None:
        assert "fixture milestone unavailable" in captured.err
        assert "work-item mirror checks failed" in output
    else:
        assert "Compose work-item evidence replays captured provider input." in row_six


@pytest.mark.parametrize(
    "mode", ["blocked", "native", "issues-mismatch", "repo-mismatch", "cache-miss"]
)
async def test_work_item_compose_replays_only_captured_reads(
    acceptance: Any, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    mirror: Any = importlib.import_module(acceptance.work_item_mirrors.__module__)
    archive: Any = importlib.import_module(acceptance.archived_checks.__module__)
    repository = "/repos/mgd43b/plotroom-test"
    live: list[str] = []
    reports: list[str] = []

    def gh(command: list[str], body: bytes) -> tuple[bytes, int]:
        assert not body
        endpoint = "/" + command[-1]
        live.append(endpoint)
        if endpoint == repository:
            data = b'{"full_name":"mgd43b/plotroom-test","visibility":"public","private":false}'
        else:
            assert endpoint in {repository + "/branches/main", repository + "/issues/25"}
            data = b"{}"
        return b"HTTP/2.0 200 OK\r\n\r\n" + data, 0

    async def tree(read: Callable[[str, str, bytes], object]) -> object:
        read("GET", repository + "/branches/main", b"")
        return mirror.EXPECTED_TREE

    async def issues(
        read: Callable[[str, str, bytes], object], _scope: object, milestone: int, child: int
    ) -> object:
        assert (milestone, child) == (6, 25)
        read("GET", repository + "/issues/25", b"")
        return WorkItem("25", "Fixture Issue")

    async def compose(
        tag: str,
        read: Callable[[str, str, bytes], object],
        *,
        module: str,
        arguments: tuple[str, ...],
        source_files: tuple[str, ...],
    ) -> dict[str, object]:
        assert tag == "fixture"
        assert module == "deploy.epic_two_mirrors"
        assert source_files == (
            "deploy/epic_two_archived.py",
            "deploy/epic_two_mirrors.py",
            "_bmad/method/scripts/tickets.py",
            ".agents/skills/bmad-agent-po/config/github-ticketing.toml",
        )
        before = live[:]
        if mode == "cache-miss":
            read("GET", repository + "/git/trees/" + mirror.ROOT_TREE, b"")
        if not arguments:
            assert mode == "blocked"
            result: dict[str, object] = await mirror.mirror_results(read, None, None, None)
        else:
            assert arguments == ("6", "25")
            result = await mirror.mirror_results(read, 6, 25, mirror.IssueScope(6, 25))
        assert live == before
        if mode == "issues-mismatch":
            result["issues"] = ["26", "Wrong issue"]
        if mode == "repo-mismatch":
            result["repo_store"] = [["1.2", "Fixture follow-up"], ["1.1", "Fixture ticket"]]
        return result

    monkeypatch.setattr(archive, "_read_gh", gh)
    monkeypatch.setattr(mirror, "tree_witness", tree)
    monkeypatch.setattr(mirror, "issues_witness", issues)
    monkeypatch.setattr(mirror, "compose_witness", compose)
    if mode in {"issues-mismatch", "repo-mismatch", "cache-miss"}:
        with pytest.raises(RuntimeError, match=r"differed|absent from the local capture"):
            await mirror.work_item_mirrors("fixture", 6, 25, reports.append)
        assert "Compose" not in " ".join(reports)
    elif mode == "blocked":
        assert not await mirror.work_item_mirrors("fixture", None, None, reports.append)
        assert [line.split(" BLOCKED")[0] for line in reports if "BLOCKED" in line] == [
            "Local GitHub Issues mirror",
            "Compose GitHub Issues mirror",
        ]
        assert live == [repository, repository + "/branches/main"]
    else:
        assert await mirror.work_item_mirrors("fixture", 6, 25, reports.append)
        assert "exact body from captured input" in reports[-1]
    if mode != "blocked":
        assert live == [repository, repository + "/branches/main", repository + "/issues/25"]


@pytest.mark.parametrize("drift", ["none", "image", "network"])
async def test_shared_compose_replay_validates_running_isolation_before_serving_frames(
    acceptance: Any, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    mirror: Any = importlib.import_module(acceptance.work_item_mirrors.__module__)
    bridge: Any = importlib.import_module("deploy.bridge_smoke")
    repo: Path = bridge.ROOT
    image = "sha256:" + "a" * 64
    witness_files = (
        "deploy/epic_two_archived.py",
        "deploy/epic_two_mirrors.py",
        "_bmad/method/scripts/tickets.py",
        ".agents/skills/bmad-agent-po/config/github-ticketing.toml",
    )
    observed: list[str] = []
    frames: list[bytes] = []
    container_name: str | None = None

    def run(command: list[str]) -> str:
        if command[:3] == ["docker", "image", "inspect"]:
            assert command[-1] == "plotroom:fixture"
            return image
        if command[:2] == ["docker", "inspect"]:
            assert container_name is not None
            assert command[-1] == container_name
            observed.append("inspected")
            deployed_image = "sha256:" + "b" * 64 if drift == "image" else image
            network = "bridge" if drift == "network" else "none"
            return f"{deployed_image} {network}"
        if command[:3] == ["docker", "ps", "-aq"]:
            return ""
        assert command[-2:] == ["down", "--volumes"]
        observed.append("cleaned")
        return ""

    class Input:
        def write(self, data: bytes) -> None:
            frames.append(data)

        async def drain(self) -> None:
            pass

    class Process:
        def __init__(self) -> None:
            self.stdin = Input()
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(
                b'{"request":["GET","/repos/mgd43b/plotroom-test",""]}\n'
                b'{"result":{"repo_store":[],"issues":null}}\n'
            )
            self.stdout.feed_eof()
            self.returncode: int | None = None

        async def wait(self) -> int:
            self.returncode = 0
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def launch(*command: str, **_options: object) -> Process:
        nonlocal container_name
        container_name = command[command.index("--name") + 1]
        config = Path(command[command.index("-f") + 1])
        raw = await asyncio.to_thread(config.read_text, encoding="utf-8")
        service = json.loads(raw)["services"]["archive"]
        assert service == {
            "image": image,
            "network_mode": "none",
            "read_only": True,
            "tmpfs": ["/tmp"],
            "working_dir": "/fixture",
            "volumes": [f"{(repo / path).resolve()}:/fixture/{path}:ro" for path in witness_files],
        }
        assert command[command.index("--pull") + 1] == "never"
        assert command[-4:] == ("archive", "-B", "-m", "deploy.epic_two_mirrors")
        return Process()

    def read(method: str, path: str, body: bytes) -> tuple[dict[str, str], bytes]:
        assert (method, path, body) == ("GET", "/repos/mgd43b/plotroom-test", b"")
        assert observed == ["inspected"]
        observed.append("replayed")
        return {}, b"{}"

    monkeypatch.setenv("DOCKER_HOST", "unix:///fixture/docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(bridge, "run", run)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    if drift == "none":
        result = await mirror.compose_mirrors("fixture", read, None, None)
        assert result == {"repo_store": [], "issues": None}
        assert frames == [b'{"headers": {}, "body": "e30="}\n']
        assert observed == ["inspected", "replayed", "cleaned"]
    else:
        with pytest.raises(RuntimeError, match="pinned isolated image"):
            await mirror.compose_mirrors("fixture", read, None, None)
        assert not frames
        assert observed == ["inspected", "cleaned"]


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
            {**child, "body": native_body.replace("\r\n", "\n")} if fault == "wrong-body" else child
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
        mismatch = "body differs from its native GitHub body" if fault == "wrong-body" else None
        with pytest.raises(RuntimeError, match=mismatch):
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


@pytest.mark.parametrize("mode", ["missing", "public", "symlink", "empty", "whitespace"])
async def test_published_mirror_requires_an_owned_private_token_file(
    acceptance: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    mirror: Any = importlib.import_module(acceptance.work_item_mirrors.__module__)
    path = tmp_path / "credential"
    if mode == "symlink":
        real = tmp_path / "real"
        real.write_text("synthetic-pat\n", encoding="utf-8")
        real.chmod(0o600)
        path.symlink_to(real)
    elif mode != "missing":
        value = {"empty": "\n", "whitespace": "synthetic pat\n"}.get(mode, "synthetic-pat\n")
        path.write_text(value, encoding="utf-8")
        path.chmod(0o644 if mode == "public" else 0o600)

    async def never_launch(*_args: object, **_kwargs: object) -> bool:
        pytest.fail("an invalid credential must not launch a witness")

    monkeypatch.setattr(mirror, "work_item_mirrors", never_launch)
    with pytest.raises(RuntimeError, match="token file"):
        await mirror.verify_published("fixture", path, print)


def test_file_backed_read_does_not_follow_redirect_or_send_unscoped_token(
    acceptance: Any, tmp_path: Path
) -> None:
    mirror: Any = importlib.import_module(acceptance.work_item_mirrors.__module__)
    path = tmp_path / "credential"
    path.write_text("unit-test-pat\n", encoding="utf-8")
    path.chmod(0o600)
    calls: list[str] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer unit-test-pat"
        return httpx.Response(302, headers={"location": "https://example.com/credential-target"})

    endpoint = "repos/mgd43b/plotroom-test"
    with httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
        assert mirror._file_backed_read(client, path, "GET", endpoint, b"") == (302, ({}, b""))
        for method, target in (("POST", endpoint), ("GET", "repos/other/repo")):
            with pytest.raises(RuntimeError, match="scoped GET"):
                mirror._file_backed_read(client, path, method, target, b"")
    assert calls == ["https://api.github.com/" + endpoint]


def test_archive_file_transport_rechecks_graphql_scope_before_sending_token(
    acceptance: Any, tmp_path: Path
) -> None:
    mirror: Any = importlib.import_module(acceptance.work_item_mirrors.__module__)
    archive: Any = importlib.import_module(acceptance.archived_checks.__module__)
    path = tmp_path / "credential"
    path.write_text("unit-test-pat\n", encoding="utf-8")
    path.chmod(0o600)
    calls: list[bytes] = []

    def read(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://api.github.com/graphql"
        assert request.headers["Authorization"] == "Bearer unit-test-pat"
        assert request.headers["Content-Type"] == "application/json"
        calls.append(request.content)
        return httpx.Response(200, json={"data": {}})

    variables = {"owner": "mgd43b", "name": "plotroom-test", "number": 2, "after": None}
    approved = json.dumps({"query": archive.REVIEW_THREADS_QUERY, "variables": variables}).encode()
    with httpx.Client(transport=httpx.MockTransport(read)) as client:
        assert (
            mirror._file_backed_read(client, path, "POST", "graphql", approved, archived=True)[0]
            == 200
        )
        for query, proof in (
            ("mutation { changed }", variables),
            (archive.REVIEW_THREADS_QUERY, {**variables, "owner": "other"}),
            (archive.REVIEW_THREADS_QUERY, {**variables, "number": 3}),
        ):
            denied = json.dumps({"query": query, "variables": proof}).encode()
            with pytest.raises(ValueError, match="outside"):
                mirror._file_backed_read(client, path, "POST", "graphql", denied, archived=True)
        duplicate_query = approved.replace(
            b'{"query":', b'{"query":"mutation { changed }","query":', 1
        )
        duplicate_variables = approved.replace(
            b'"variables": {', b'"variables": {},"variables": {', 1
        )
        duplicate_owner = approved.replace(b'"owner":', b'"owner":"other","owner":', 1)
        for denied in (duplicate_query, duplicate_variables, duplicate_owner):
            with pytest.raises(ValueError, match="duplicate property"):
                mirror._file_backed_read(client, path, "POST", "graphql", denied, archived=True)
    assert calls == [approved]


@pytest.mark.parametrize(
    ("method", "endpoint", "status", "kind", "expected"),
    [
        (
            "GET",
            "repos/mgd43b/plotroom-test/commits/" + "a" * 40 + "/check-runs?private=query",
            403,
            "FORBIDDEN",
            "read returned HTTP 403 at GET /repos/mgd43b/plotroom-test/commits/"
            + "a" * 40
            + "/check-runs",
        ),
        (
            "POST",
            "graphql",
            401,
            "UNKNOWN",
            "read returned HTTP 401 at GraphQL review threads for PR #2",
        ),
        (
            "POST",
            "graphql",
            200,
            "UNKNOWN",
            "read returned errors at GraphQL review threads for PR #2",
        ),
        ("POST", "graphql", 200, "FORBIDDEN", "access denied at GraphQL review threads for PR #2"),
    ],
)
def test_archive_file_transport_sanitizes_provider_denials(
    acceptance: Any,
    tmp_path: Path,
    method: str,
    endpoint: str,
    status: int,
    kind: str,
    expected: str,
) -> None:
    mirror: Any = importlib.import_module(acceptance.work_item_mirrors.__module__)
    archive: Any = importlib.import_module(acceptance.archived_checks.__module__)
    path = tmp_path / "credential"
    path.write_text("unit-test-pat\n", encoding="utf-8")
    path.chmod(0o600)
    document = json.dumps(
        {
            "query": archive.REVIEW_THREADS_QUERY,
            "variables": {"owner": "mgd43b", "name": "plotroom-test", "number": 2, "after": None},
        }
    ).encode()

    calls = 0

    def denied(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"errors": [{"message": "unit-test-pat", "type": kind}]})

    with httpx.Client(transport=httpx.MockTransport(denied)) as client:

        def transport(
            verb: str, target: str, payload: bytes
        ) -> tuple[int, tuple[dict[str, str], bytes]]:
            return mirror._file_backed_read(client, path, verb, target, payload, archived=True)

        provider = archive.ProviderReads(transport=transport, describe_archive_errors=True)
        with pytest.raises(RuntimeError) as error:
            provider.read(method, f"/{endpoint}", document if method == "POST" else b"")
        with pytest.raises(RuntimeError, match="read budget exhausted"):
            provider.read("GET", "/repos/mgd43b/plotroom-test/branches/main", b"")
    assert calls == 1
    assert str(error.value) == f"GitHub fixture {expected}"


@pytest.mark.parametrize("fault", [None, "denied", "body-drift", "epic-drift", "replay-drift"])
async def test_published_mirror_captures_auth_reads_and_replays_without_credentials(
    acceptance: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str | None,
) -> None:
    mirror: Any = importlib.import_module(acceptance.work_item_mirrors.__module__)
    archive: Any = importlib.import_module(acceptance.archived_checks.__module__)
    path = tmp_path / "credential"
    path.write_text("unit-test-pat\n", encoding="utf-8")
    path.chmod(0o600)
    repo = "/repos/mgd43b/plotroom-test"
    native_body = "```yaml\r\ntype: story\r\nrefined: true\r\n```\r\n\r\nSynthetic 📌\r\n"
    monkeypatch.setattr(mirror, "PUBLISHED_BODY", hashlib.sha256(native_body.encode()).hexdigest())
    now = "2026-11-27T19:00:00Z"
    epic = {
        "number": 8 if fault == "epic-drift" else 6,
        "title": "Row 6 fixture epic",
        "state": "open",
        "body": "",
        "milestone": {"number": 1},
        "labels": [{"name": "epic"}, {"name": "backlog"}],
        "assignees": [],
        "updated_at": now,
    }
    child = {
        "number": 7,
        "title": "Row 6 fixture story",
        "state": "open",
        "body": native_body.replace("\r\n", "\n") if fault == "body-drift" else native_body,
        "milestone": None,
        "labels": [{"name": "story"}, {"name": "backlog"}],
        "assignees": [],
        "updated_at": now,
    }
    relation = f"{repo}/issues/6/sub_issues?per_page=100&page=1"
    data = {
        repo: {"full_name": "mgd43b/plotroom-test", "visibility": "public", "private": False},
        f"{repo}/branches/main": {},
        f"{repo}/milestones/1": {
            "number": 1,
            "state": "open",
            "title": "Plotroom #46 row 6 fixture",
        },
        f"{repo}/issues/7": child,
        f"{repo}/issues?milestone=1&state=all&per_page=100&page=1": [epic],
        relation: [{"number": 7, "repository_url": f"https://api.github.com{repo}"}],
        f"{repo}/issues/7/sub_issues?per_page=100&page=1": [],
        f"{repo}/issues/6/dependencies/blocked_by?per_page=100&page=1": [],
        f"{repo}/issues/7/dependencies/blocked_by?per_page=100&page=1": [],
    }
    calls: list[str] = []
    reports: list[str] = []
    compose_calls: list[tuple[int, int]] = []

    def read(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        assert request.headers["Authorization"] == "Bearer unit-test-pat"
        target = request.url.raw_path.decode()
        calls.append(target)
        if target == relation and fault == "denied":
            return httpx.Response(403, json={"message": "unit-test-pat"})
        return httpx.Response(
            200, json=data[target], headers={"authorization": "never-replay-this"}
        )

    real_client = httpx.Client

    def client(**options: Any) -> httpx.Client:
        assert options == {"timeout": 9, "follow_redirects": False, "trust_env": False}
        return real_client(transport=httpx.MockTransport(read), **options)

    async def tree(reader: Callable[[str, str, bytes], tuple[dict[str, str], bytes]]) -> object:
        headers, _ = reader("GET", f"{repo}/branches/main", b"")
        assert not headers
        return mirror.EXPECTED_TREE

    async def compose(
        tag: str,
        reader: Callable[[str, str, bytes], tuple[dict[str, str], bytes]],
        milestone: int,
        issue: int,
    ) -> dict[str, object]:
        assert tag == "fixture"
        compose_calls.append((milestone, issue))
        before = calls[:]
        observed: dict[str, object] = await mirror.mirror_results(
            reader, milestone, issue, mirror.IssueScope(milestone, issue)
        )
        assert calls == before
        if fault == "replay-drift":
            observed["issues"] = None
        return observed

    def never_use_gh(*_args: object) -> tuple[bytes, int]:
        pytest.fail("the published witness must not use the gh auth session")

    monkeypatch.setattr(mirror.httpx, "Client", client)
    monkeypatch.setattr(mirror, "tree_witness", tree)
    monkeypatch.setattr(mirror, "compose_mirrors", compose)
    monkeypatch.setattr(archive, "_read_gh", never_use_gh)
    if fault is None:
        await mirror.verify_published("fixture", path, reports.append)
        assert compose_calls == [(1, 7)]
        assert set(calls) == set(data)
        assert any("credential-backed witness PASS" in report for report in reports)
    else:
        message = {
            "body-drift": "story metadata or body differs",
            "epic-drift": "published fixture epic differs",
            "replay-drift": "Compose work-item mirrors differed",
        }.get(fault)
        with pytest.raises(RuntimeError, match=message) as error:
            await mirror.verify_published("fixture", path, reports.append)
        if fault == "denied":
            assert str(error.value) == "GitHub fixture read returned HTTP 403"
        assert compose_calls == ([(1, 7)] if fault == "replay-drift" else [])
        assert not any("credential-backed witness PASS" in report for report in reports)
        assert not any("unit-test-pat" in report for report in reports)
        if fault in {"body-drift", "epic-drift"}:
            assert not any("sub_issues" in endpoint for endpoint in calls)
