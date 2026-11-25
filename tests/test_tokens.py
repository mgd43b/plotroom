"""The offline token files and the request-time identity lookup."""

from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event
from typing import Any

import httpx
import pytest

from conftest import Child, authenticated_client
from plotroom.app import tokens as token_store
from plotroom.app.cli import EXIT_CONFIG, EXIT_FAILURE, main
from plotroom.app.lock import LOCK_FILE_NAME
from plotroom.app.tokens import (
    IssuedToken,
    TokenError,
    TokenRegistry,
    local_token_path,
    select_local_token,
)
from plotroom.core.roles import Identity

pytestmark = pytest.mark.usefixtures("token_home")


async def test_offline_cli_issues_and_revokes_without_exposing_bearers_in_the_instance(
    config_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = config_path.parent / "data"
    registry = TokenRegistry(data_dir)
    assert (
        main(
            [
                "token",
                "issue",
                "--config",
                str(config_path),
                "--project",
                "demo",
                "--role",
                "worker:ada",
            ]
        )
        == 0
    )
    capture = capsys.readouterr()
    response: dict[str, str] = json.loads(capture.out)
    bearer, token_id = response["token"], response["token_id"]
    assert bearer not in capture.err
    stored = registry.path.read_text(encoding="utf-8")
    assert bearer not in stored
    assert json.loads(stored) == {
        token_id: {
            "hash": hashlib.sha256(bearer.encode()).hexdigest(),
            "project": "demo",
            "role": "worker:ada",
        },
    }
    local = local_token_path()
    assert stat.S_IMODE(local.stat().st_mode) == 0o600
    assert stat.S_IMODE(registry.path.stat().st_mode) == 0o600
    assert await registry.authenticate(bearer) == Identity("demo", "worker:ada")
    assert select_local_token(data_dir, token_id) == bearer
    assert not (data_dir / LOCK_FILE_NAME).exists(), "token operation ran without a daemon"

    assert main(["token", "revoke", "--config", str(config_path), token_id]) == 0
    assert bearer not in capsys.readouterr().err
    assert await registry.authenticate(bearer) is None
    assert json.loads(registry.path.read_text(encoding="utf-8")) == {}
    assert bearer not in local.read_text(encoding="utf-8")
    assert stat.S_IMODE(local.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("failure", "exit_code"), [("permissions", EXIT_CONFIG), ("write", EXIT_FAILURE)]
)
async def test_revoke_reports_local_cleanup_failure_and_the_same_command_retries_it(
    config_path: Path,
    server: tuple[Child, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
    exit_code: int,
) -> None:
    _, url = server
    data_dir = config_path.parent / "data"
    registry = TokenRegistry(data_dir)
    token = registry.issue("demo", "orchestrator")
    local = local_token_path()
    revoke = ["token", "revoke", "--config", str(config_path), token.token_id]
    async with authenticated_client(url, token.bearer) as client:
        assert (await client.call_tool("snapshot", {})).is_error is False
    replace = os.replace

    def fail_only_local(source: str, target: Path) -> None:
        if target == local:
            raise OSError("local write was refused")
        replace(source, target)

    with monkeypatch.context() as patch:
        if failure == "permissions":
            local.chmod(0o644)
        else:
            patch.setattr(os, "replace", fail_only_local)
        assert main(revoke) == exit_code
    local.chmod(0o600)
    output = capsys.readouterr()
    assert "token is revoked" in output.err
    assert "local credential cleanup failed" in output.err
    assert "retry the same revoke command" in output.err
    assert token.bearer not in output.err + output.out
    assert select_local_token(data_dir, token.token_id) == token.bearer

    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(url, headers={"authorization": f"Bearer {token.bearer}"})
    assert response.status_code == 401
    assert response.content == b""
    assert main(revoke) == 0
    assert select_local_token(data_dir, None) is None
    assert await registry.authenticate(token.bearer) is None


async def test_no_local_revokes_even_when_the_local_snapshot_is_invalid(config_path: Path) -> None:
    registry = TokenRegistry(config_path.parent / "data")
    token = registry.issue("demo", "orchestrator")
    local = local_token_path()
    local_contents = b"not JSON"
    local.write_bytes(local_contents)
    before = local.stat()
    assert (
        main(["token", "revoke", "--no-local", "--config", str(config_path), token.token_id]) == 0
    )
    assert local.read_bytes() == local_contents
    assert local.stat().st_ino == before.st_ino
    assert await registry.authenticate(token.bearer) is None


@pytest.mark.parametrize(
    "invalid", ["encoding", "json", "nesting", "record", "symlink", "directory"]
)
async def test_invalid_instance_snapshots_fail_closed_in_cli_and_http(
    config_path: Path,
    server: tuple[Child, str],
    server_token: IssuedToken,
    capsys: pytest.CaptureFixture[str],
    invalid: str,
) -> None:
    _, url = server
    path = TokenRegistry(config_path.parent / "data").path
    valid = path.read_bytes()
    if invalid == "encoding":
        path.write_bytes(b"\xff" + server_token.bearer.encode())
    elif invalid == "json":
        path.write_text(f"{{malformed {server_token.bearer}", encoding="utf-8")
    elif invalid == "nesting":
        path.write_text("[" * 2048 + json.dumps(server_token.bearer) + "]" * 2048, encoding="utf-8")
    elif invalid == "record":
        records = json.loads(valid)
        records["f" * 24] = {"hash": server_token.bearer, "project": "demo", "role": "orchestrator"}
        path.write_text(json.dumps(records), encoding="utf-8")
    else:
        path.unlink()
        if invalid == "symlink":
            other = path.with_name("other.json")
            other.write_bytes(valid)
            other.chmod(0o600)
            path.symlink_to(other)
        else:
            path.mkdir(mode=0o700)

    assert (
        main(["token", "revoke", "--no-local", "--config", str(config_path), server_token.token_id])
        == EXIT_CONFIG
    )
    output = capsys.readouterr()
    assert "cannot manage token" in output.err
    assert server_token.bearer not in output.err + output.out
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(
            url, headers={"authorization": f"Bearer {server_token.bearer}"}
        )
    assert response.status_code == 401
    assert response.content == b""


@pytest.mark.parametrize("contents", ["[" * 2048 + "0" + "]" * 2048, '{"unrelated-dir": false}'])
def test_invalid_local_snapshots_report_configuration_errors_at_connect(
    config_path: Path, capsys: pytest.CaptureFixture[str], contents: str
) -> None:
    token = TokenRegistry(config_path.parent / "data").issue("demo", "orchestrator")
    local_token_path().write_text(contents, encoding="utf-8")
    assert main(["connect", "--stdio", "--config", str(config_path)]) == EXIT_CONFIG
    output = capsys.readouterr()
    assert "invalid connection configuration" in output.err
    assert "invalid" in json.loads(output.err)["reason"]
    assert token.bearer not in output.err + output.out


async def test_token_read_io_errors_remain_operational(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = TokenRegistry(config_path.parent / "data")
    token = registry.issue("demo", "orchestrator", local=False)
    open_file = os.open

    def fail_read(
        name: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if os.fsdecode(name) == str(registry.path) and flags & os.O_NONBLOCK:
            raise OSError(errno.EIO, "input/output error")
        return open_file(name, flags, mode, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", fail_read)
        assert await registry.authenticate(token.bearer) is None
        assert (
            main(["token", "revoke", "--no-local", "--config", str(config_path), token.token_id])
            == EXIT_FAILURE
        )
    output = capsys.readouterr()
    assert "input/output error" in output.err
    assert token.bearer not in output.err + output.out
    assert await registry.authenticate(token.bearer) == Identity("demo", "orchestrator")


async def test_rejected_bearers_skip_whole_snapshot_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = TokenRegistry(tmp_path)
    token = registry.issue("demo", "orchestrator", local=False)
    token_id, secret = token.bearer.split(".", 1)
    missing_id = "f" * 24 if token_id != "f" * 24 else "0" * 24
    wrong_secret = ("f" if secret[0] != "f" else "0") + secret[1:]

    def unexpected_validation(_path: Path, _records: dict[str, Any]) -> None:
        pytest.fail("requests with a missing ID or bad digest do not validate the full snapshot")

    with monkeypatch.context() as patch:
        patch.setattr("plotroom.app.tokens._validate_instance", unexpected_validation)
        assert await registry.authenticate(f"{missing_id}.{secret}") is None
        assert await registry.authenticate(f"{token_id}.{wrong_secret}") is None
    assert await registry.authenticate(token.bearer) == Identity("demo", "orchestrator")


@pytest.mark.parametrize("role", ["system", "worker", "worker:bad name", "admin"])
def test_cli_refuses_invalid_token_roles(config_path: Path, role: str) -> None:
    code = main(
        ["token", "issue", "--config", str(config_path), "--project", "demo", "--role", role]
    )
    assert code == EXIT_CONFIG
    assert not local_token_path().exists()


def test_cli_refuses_to_issue_for_an_unknown_project(config_path: Path) -> None:
    code = main(
        ["token", "issue", "--config", str(config_path), "--project", "beta", "--role", "clerk"]
    )
    assert code == EXIT_CONFIG
    assert not local_token_path().exists()


def test_local_selection_is_scoped_to_a_data_directory_and_can_choose_by_id(
    tmp_path: Path,
) -> None:
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    first = TokenRegistry(alpha).issue("demo", "clerk")
    other = TokenRegistry(beta).issue("demo", "orchestrator")
    assert select_local_token(alpha, None) == first.bearer
    assert select_local_token(beta, None) == other.bearer
    with pytest.raises(TokenError, match="no local token"):
        select_local_token(alpha, other.token_id)
    additional = TokenRegistry(alpha).issue("demo", "drain")
    with pytest.raises(TokenError, match="several local tokens"):
        select_local_token(alpha, None)
    assert select_local_token(alpha, additional.token_id) == additional.bearer


def test_instance_and_local_files_are_distinct_even_in_the_same_directory() -> None:
    registry = TokenRegistry(local_token_path().parent)
    assert registry.path != local_token_path()
    token = registry.issue("demo", "bridge")
    assert select_local_token(registry.path.parent, token.token_id) == token.bearer
    registry.revoke(token.token_id)
    assert select_local_token(registry.path.parent, None) is None


def test_remote_issue_does_not_save_a_local_copy(tmp_path: Path) -> None:
    registry = TokenRegistry(tmp_path)
    registry.issue("demo", "orchestrator", local=False)
    assert not local_token_path().exists()


def test_local_selection_refuses_a_file_that_is_not_private(tmp_path: Path) -> None:
    TokenRegistry(tmp_path).issue("demo", "orchestrator")
    local_token_path().chmod(0o644)
    with pytest.raises(TokenError, match="mode 0600"):
        select_local_token(tmp_path, None)


def test_local_publication_follows_the_instance_write_and_rolls_back_a_failed_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = TokenRegistry(tmp_path)
    local = local_token_path()
    replace = os.replace
    publications: list[Path] = []

    def publish(source: str, target: Path) -> None:
        publications.append(target)
        if target == local:
            assert len(json.loads(registry.path.read_text(encoding="utf-8"))) == 1
            raise OSError("local publication failed")
        replace(source, target)

    monkeypatch.setattr(os, "replace", publish)
    with pytest.raises(OSError, match="local publication failed"):
        registry.issue("demo", "orchestrator")

    assert publications == [registry.path, local, registry.path]
    assert json.loads(registry.path.read_text(encoding="utf-8")) == {}
    assert select_local_token(tmp_path, None) is None


async def test_concurrent_token_writes_do_not_lose_credentials(tmp_path: Path) -> None:
    registry = TokenRegistry(tmp_path)
    issued = await asyncio.gather(
        asyncio.to_thread(registry.issue, "demo", "orchestrator"),
        asyncio.to_thread(registry.issue, "demo", "clerk"),
    )
    entries: dict[str, Any] = json.loads(registry.path.read_text(encoding="utf-8"))
    assert set(entries) == {token.token_id for token in issued}
    assert select_local_token(tmp_path, issued[0].token_id) == issued[0].bearer
    assert select_local_token(tmp_path, issued[1].token_id) == issued[1].bearer


async def test_concurrent_local_tokens_stay_scoped_to_their_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha, beta = TokenRegistry(tmp_path / "alpha"), TokenRegistry(tmp_path / "beta")
    local = local_token_path()
    contended = Event()
    start = Barrier(2, action=contended.set)
    lock = token_store._locked  # pyright: ignore[reportPrivateUsage]
    read = token_store._read_local  # pyright: ignore[reportPrivateUsage]

    @contextmanager
    def contend(path: Path) -> Generator[None]:
        if path == local:
            start.wait(timeout=10)
        with lock(path):
            yield

    def read_while_locked(path: Path) -> dict[str, dict[str, str]]:
        # A separate file description must contend with the writer's kernel lock.
        with path.with_suffix(".lock").open("rb") as handle, pytest.raises(BlockingIOError):
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return read(path)

    with monkeypatch.context() as patch:
        patch.setattr(token_store, "_locked", contend)
        patch.setattr(token_store, "_read_local", read_while_locked)
        first, second = await asyncio.gather(
            asyncio.to_thread(alpha.issue, "demo", "clerk"),
            asyncio.to_thread(beta.issue, "demo", "orchestrator"),
        )
    assert contended.is_set(), "both issuers must reach the shared registry lock"
    for registry, own, other, role in (
        (alpha, first, second, "clerk"),
        (beta, second, first, "orchestrator"),
    ):
        directory = registry.path.parent
        assert select_local_token(directory, None) == own.bearer
        assert select_local_token(directory, own.token_id) == own.bearer
        assert await registry.authenticate(own.bearer) == Identity("demo", role)
        assert await registry.authenticate(other.bearer) is None
        with pytest.raises(TokenError, match="no local token"):
            select_local_token(directory, other.token_id)


async def test_invalid_instance_file_fails_closed_and_recovers_on_replacement(
    tmp_path: Path,
) -> None:
    registry = TokenRegistry(tmp_path)
    token = registry.issue("demo", "orchestrator", local=False)
    contents = registry.path.read_bytes()
    registry.path.write_text("{invalid JSON", encoding="utf-8")
    assert await registry.authenticate(token.bearer) is None
    registry.path.write_bytes(contents)
    assert await registry.authenticate(token.bearer) == Identity("demo", "orchestrator")
