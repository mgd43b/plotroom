"""Shared fixtures: config files and real ``plotroom`` subprocesses driven without sleeps."""

from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import TextContent

from plotroom.app.tokens import IssuedToken, TokenRegistry

CHILD_TIMEOUT = 60.0


def write_config(
    directory: Path,
    *,
    projects: tuple[str, ...] = ("demo",),
    port: int = 0,
    name: str = "config",
    log_max_bytes: int | None = None,
    log_backups: int | None = None,
) -> Path:
    """A config file with its own data directory and the given projects.

    Port 0 (the default) lets the daemon take any free port and publish it; a test that needs a
    fixed port passes one it holds (``held_port`` or a listening socket), never one it picked.
    """
    path = directory / f"{name}.toml"
    tables = "\n".join(f"[projects.{project}]" for project in projects)
    settings = ""
    if log_max_bytes is not None:
        settings += f"log_max_bytes = {log_max_bytes}\n"
    if log_backups is not None:
        settings += f"log_backups = {log_backups}\n"
    path.write_text(
        f'data_dir = "{directory / "data"}"\nhost = "127.0.0.1"\nport = {port}\n{settings}\n{tables}\n',
        encoding="utf-8",
    )
    return path


def child_env() -> dict[str, str]:
    """The test process's environment without any PLOTROOM_* setting leaking in."""
    return {k: v for k, v in os.environ.items() if not k.startswith("PLOTROOM_")}


@dataclass
class Child:
    """A running ``plotroom`` process whose JSON log lines are read through a queue."""

    process: subprocess.Popen[str]
    lines: queue.Queue[str | None] = field(default_factory=lambda: queue.Queue[str | None]())
    seen: list[dict[str, Any]] = field(default_factory=lambda: [])

    def __post_init__(self) -> None:
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def wait_for_log(self, msg: str) -> dict[str, Any]:
        """The first log line whose ``msg`` matches; fails with what was seen if the child ends."""
        while True:
            try:
                line = self.lines.get(timeout=CHILD_TIMEOUT)
            except queue.Empty:
                pytest.fail(f"no {msg!r} log line within {CHILD_TIMEOUT}s; saw {self.seen}")
            if line is None:
                pytest.fail(f"plotroom exited before logging {msg!r}; saw {self.seen}")
            entry: dict[str, Any] = json.loads(line)
            self.seen.append(entry)
            if entry.get("msg") == msg:
                return entry

    def stop(self) -> int:
        if self.process.poll() is None:
            self.process.terminate()
        try:
            return self.process.wait(timeout=CHILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait()


def start_plotroom(*args: str) -> Child:
    process = subprocess.Popen(
        [sys.executable, "-m", "plotroom", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=child_env(),
    )
    return Child(process)


@pytest.fixture
def held_port() -> Iterator[int]:
    """A port that refuses connections and that no one else can take while the test runs.

    The socket is bound and never listens: a connect to it is refused, and the kernel will not
    hand the port to another program until the test ends.
    """
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        yield int(holder.getsockname()[1])


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return write_config(tmp_path)


@pytest.fixture
def token_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def server_token(config_path: Path) -> IssuedToken:
    return TokenRegistry(config_path.parent / "data").issue("demo", "orchestrator", local=False)


@pytest.fixture
def server(config_path: Path, server_token: IssuedToken) -> Iterator[tuple[Child, str]]:
    """A running ``plotroom serve`` and its MCP URL."""
    child = start_plotroom("serve", "--config", str(config_path))
    try:
        serving = child.wait_for_log("serving")
        yield child, f"http://127.0.0.1:{serving['port']}/mcp"
    finally:
        child.stop()


@asynccontextmanager
async def authenticated_client(
    url: str, bearer: str, *, mode: str = "legacy"
) -> AsyncGenerator[Client]:
    async with (
        httpx2.AsyncClient(headers={"authorization": f"Bearer {bearer}"}) as http,
        Client(streamable_http_client(url, http_client=http), mode=mode) as client,
    ):
        yield client


def tool_json(result: Any) -> dict[str, Any]:
    """The JSON object a tool call returned as text."""
    content = result.content[0]
    assert isinstance(content, TextContent)
    decoded: dict[str, Any] = json.loads(content.text)
    return decoded
