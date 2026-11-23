"""The offline token files and the request-time identity lookup."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from plotroom.app.cli import EXIT_CONFIG, main
from plotroom.app.lock import LOCK_FILE_NAME
from plotroom.app.tokens import TokenError, TokenRegistry, local_token_path, select_local_token
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
