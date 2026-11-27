"""Observe pinned, archived GitHub facts with the production daemon."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import re
import secrets
import selectors
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, closing, suppress
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from plotroom.adapters.github import github_secret_name
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.read import PULL_MERGE_SHA_QUERY, REVIEW_THREADS_QUERY
from plotroom.app.config import load_config
from plotroom.app.serve import Daemon
from plotroom.core.events import Event
from plotroom.core.github import PULL_REQUESTS
from plotroom.core.github_projection import CHECKS, THREADS
from plotroom.core.ledger import Ledger

REPO = "mgd43b/plotroom-test"
PROJECT = "archive-witness"
MERGED_HEAD = "6972a064fb370b26d35bab6af54cc49823828f30"
MERGED_BRANCH = "plotroom-fixture/45845942573"
MERGED_COMMIT = "9a6ce8a89a15046c18dbfa5d7d2af88bb0edbc14"
REVIEW_HEAD = "3b130ec9573e8ac6829bc81af85c346fb79bdeb8"
REVIEW_BRANCH = "plotroom-33/acceptance-20261126"
THREAD = "PRRT_kwDOV88CwM68exro"
RUN = 45859680348
CHECK_NAMES = {140047120493: "Fixture checks", 140047120193: "Fixture image build"}
MAX_READS = 65
MAX_RESPONSE = 4 * 1024 * 1024
MAX_CAPTURE = 16 * 1024 * 1024
MAX_FRAME = 2 * MAX_RESPONSE
SAFE_HEADERS = {"link", "x-ratelimit-remaining", "x-ratelimit-reset", "retry-after"}
FIXTURE_PR_NUMBERS = (1, 2, 4, 5)
REST_RESOURCE = re.compile(
    r"(?:/branches/main|/pulls(?:/[1245](?:/files)?)?"
    r"|/commits/[0-9a-f]{40}/check-runs|/actions/runs(?:/\d+(?:/attempts/\d+(?:/jobs)?)?)?"
    r"|/issues(?:/comments|/[1-5]/(?:labels|events|comments))?|/deployments)?"
)
Response = tuple[dict[str, str], bytes]
Reader = Callable[[str, str, bytes], Response]
ReadTransport = Callable[[str, str, bytes], tuple[int, Response]]


class ArchiveReadError(RuntimeError):
    """An actionable fixture read error containing only a controlled message."""


def _unique_graphql_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate property in fixture GraphQL request")
    return result


def _scoped_endpoint(method: str, path: str, body: bytes) -> str:
    parsed = urlsplit(path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or len(path) > 2048
        or any(not 33 <= ord(character) <= 126 for character in path)
    ):
        raise ValueError("unsupported fixture request target")
    if method == "GET" and not body:
        root = f"/repos/{REPO}"
        if parsed.path.startswith(root) and REST_RESOURCE.fullmatch(parsed.path[len(root) :]):
            return path[1:]
    if method == "POST" and path == "/graphql" and len(body) <= 24000:
        document = json.loads(body, object_pairs_hook=_unique_graphql_object)
        if not isinstance(document, dict) or set(document) != {"query", "variables"}:
            raise ValueError("unexpected fixture GraphQL envelope")
        variables = document["variables"]
        query = document["query"]
        required = {"owner", "name", "number"}
        if query == REVIEW_THREADS_QUERY:
            required.add("after")
        if (
            query in (REVIEW_THREADS_QUERY, PULL_MERGE_SHA_QUERY)
            and isinstance(variables, dict)
            and set(variables) == required
            and variables["owner"] == "mgd43b"
            and variables["name"] == "plotroom-test"
            and type(variables["number"]) is int
            and variables["number"] in FIXTURE_PR_NUMBERS
            and (
                variables.get("after") is None
                or (isinstance(variables["after"], str) and len(variables["after"]) <= 512)
            )
        ):
            return "graphql"
    raise ValueError("request outside the archived fixture read scope")


def archive_read_boundary(method: str, endpoint: str, body: bytes) -> str:
    """Describe an already-allowlisted read without including a query or payload."""
    if method == "GET":
        return f"GET /{urlsplit(endpoint).path}"
    document = json.loads(body)
    operation = "review threads" if document["query"] == REVIEW_THREADS_QUERY else "merged PR head"
    return f"GraphQL {operation} for PR #{document['variables']['number']}"


def _read_gh(command: list[str], body: bytes) -> tuple[bytes, int]:
    deadline = time.monotonic() + 9
    output = bytearray()
    max_http_bytes = MAX_RESPONSE + 65536
    with tempfile.TemporaryFile() as request:
        request.write(body)
        request.seek(0)
        with subprocess.Popen(  # noqa: S603 - fixed executable and validated request scope
            command,
            stdin=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={
                **os.environ,
                "GH_DEBUG": "",
                "GH_PROMPT_DISABLED": "1",
                "GH_NO_UPDATE_NOTIFIER": "1",
            },
        ) as child:
            assert child.stdout is not None
            try:
                with selectors.DefaultSelector() as ready:
                    ready.register(child.stdout, selectors.EVENT_READ)
                    while ready.select(max(0.0, deadline - time.monotonic())):
                        chunk = os.read(
                            child.stdout.fileno(), min(65536, max_http_bytes - len(output) + 1)
                        )
                        if not chunk:
                            return bytes(output), child.wait(
                                timeout=max(0.0, deadline - time.monotonic())
                            )
                        output.extend(chunk)
                        if len(output) > max_http_bytes:
                            raise ArchiveReadError(
                                "GitHub fixture HTTP response exceeded the byte bound"
                            )
                        if time.monotonic() >= deadline:
                            break
                raise ArchiveReadError("GitHub fixture read exceeded the time bound")
            finally:
                if child.poll() is None:
                    with suppress(ProcessLookupError):
                        child.kill()


def _gh_transport(method: str, endpoint: str, body: bytes) -> tuple[int, Response]:
    command = [
        "gh",
        "api",
        "--include",
        "--hostname",
        "github.com",
        "--method",
        method,
        "-H",
        "X-GitHub-Api-Version: 2026-03-10",
        endpoint,
    ]
    if method == "POST":
        command += ["--input", "-"]
    stdout, returncode = _read_gh(command, body)
    parts = re.split(rb"\r?\n\r?\n", stdout, maxsplit=1)
    lines = parts[0].splitlines()
    if not lines or not (status := re.match(rb"HTTP/[0-9.]+ (\d{3})", lines[0])):
        raise ArchiveReadError("gh did not return a GitHub fixture HTTP response")
    code = int(status[1])
    if returncode and code == 200:
        raise ArchiveReadError("gh exited unsuccessfully while reading a fixture")
    if code != 200:
        return code, ({}, b"")
    if len(parts) != 2:
        raise ArchiveReadError("gh did not return a GitHub fixture response body")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(b":")
        key = name.decode("ascii").lower()
        if key in SAFE_HEADERS:
            headers[key] = value.decode("ascii").strip()
    return code, (headers, parts[1])


class ProviderReads:
    def __init__(
        self,
        scope: Callable[[str, str, bytes], str] = _scoped_endpoint,
        *,
        transport: ReadTransport | None = None,
        describe_archive_errors: bool = False,
    ) -> None:
        self._scope = scope
        self._transport = _gh_transport if transport is None else transport
        self._describe_archive_errors = describe_archive_errors
        self._cache: dict[tuple[str, str, bytes], Response] = {}
        self.live = 0
        self.reused = 0
        self._bytes = 0
        self._lock = threading.Lock()
        self._deadline = time.monotonic() + 100

    def preflight_public(self) -> None:
        prefix = f"/repos/{REPO}"
        _, raw = self.read("GET", prefix, b"")
        repo = json.loads(raw)
        if (repo.get("full_name"), repo.get("visibility"), repo.get("private")) != (
            REPO,
            "public",
            False,
        ):
            raise ArchiveReadError(
                "the dedicated GitHub fixture is not public or its identity changed"
            )

    def preflight(self) -> None:
        self.preflight_public()
        prefix = f"/repos/{REPO}"
        _, raw = self.read("GET", f"{prefix}/actions/runs/{RUN}", b"")
        run = json.loads(raw)
        if (
            run.get("id"),
            run.get("run_attempt"),
            run.get("head_sha"),
            run.get("head_branch"),
            run.get("event"),
            run.get("status"),
            run.get("conclusion"),
        ) != (RUN, 1, REVIEW_HEAD, REVIEW_BRANCH, "pull_request", "completed", "success"):
            raise ArchiveReadError("the pinned archived Actions run did not match")

    def read(self, method: str, path: str, body: bytes) -> Response:
        endpoint = self._scope(method, path, body)
        boundary = (
            f" at {archive_read_boundary(method, endpoint, body)}"
            if self._describe_archive_errors
            else ""
        )
        key = method, path, body
        with self._lock:
            if key in self._cache:
                self.reused += 1
                return self._cache[key]
            if self.live >= MAX_READS or time.monotonic() >= self._deadline:
                raise ArchiveReadError("GitHub fixture read budget exhausted")
            self.live += 1
            code, (headers, payload) = self._transport(method, endpoint, body)
            if code != 200:
                if code in (403, 429) or (boundary and code == 401):
                    self._deadline = 0
                raise ArchiveReadError(f"GitHub fixture read returned HTTP {code}{boundary}")
            if len(payload) > MAX_RESPONSE or self._bytes + len(payload) > MAX_CAPTURE:
                raise ArchiveReadError("GitHub fixture capture exceeded the configured byte bound")
            document = json.loads(payload)
            if (
                boundary
                and method == "POST"
                and isinstance(document, dict)
                and (errors := document.get("errors"))
            ):
                self._deadline = 0
                if isinstance(errors, list) and any(
                    isinstance(error, dict) and error.get("type") == "FORBIDDEN" for error in errors
                ):
                    raise ArchiveReadError(f"GitHub fixture access denied{boundary}")
                raise ArchiveReadError(f"GitHub fixture read returned errors{boundary}")
            response = (
                {
                    name.lower(): value
                    for name, value in headers.items()
                    if name.lower() in SAFE_HEADERS
                },
                payload,
            )
            self._cache[key] = response
            self._bytes += len(payload)
            return response

    def replay(self, method: str, path: str, body: bytes) -> Response:
        self._scope(method, path, body)
        with self._lock:
            response = self._cache.get((method, path, body))
            if response is None:
                raise ArchiveReadError("Compose requested a response absent from the local capture")
            self.reused += 1
            return response


@asynccontextmanager
async def loopback(read: Reader, marker: str) -> AsyncIterator[tuple[str, list[str]]]:
    errors: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def do_GET(self) -> None:
            self.observe("GET")

        def do_POST(self) -> None:
            self.observe("POST")

        def observe(self, method: str) -> None:
            offered = self.headers.get("Authorization", "").encode("utf-8")
            if not hmac.compare_digest(offered, f"Bearer {marker}".encode()):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 <= length <= 24000 or (method == "GET" and length):
                    raise ValueError("unapproved loopback request size")
                headers, data = read(method, self.path, self.rfile.read(length))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)
            except BrokenPipeError, ConnectionResetError:
                return
            except Exception as error:
                reason = str(error) if isinstance(error, ArchiveReadError) else type(error).__name__
                errors.append(reason)
                self.send_error(502)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", errors
        finally:
            await asyncio.to_thread(server.shutdown)
            thread.join(timeout=2)


@asynccontextmanager
async def daemon(config: Path) -> AsyncIterator[Ledger]:
    instance = Daemon(load_config(config, {}))
    task = asyncio.create_task(instance.run())
    try:
        async with asyncio.timeout(10):
            await instance.wait_started()
        yield instance.ledgers[PROJECT]
    finally:
        instance.stop()
        async with asyncio.timeout(10):
            await task


async def witness(read: Reader) -> dict[str, int]:
    if not __debug__:
        raise RuntimeError("the archived witness requires Python assertions")
    if github_secret_name(PROJECT) in os.environ:
        raise RuntimeError("an archive fixture credential override would replace its local marker")
    marker = secrets.token_urlsafe(24)
    async with loopback(read, marker) as (endpoint, errors):
        try:
            with tempfile.TemporaryDirectory(prefix="plotroom-epic-two-archive-") as directory:
                result = await project_witness(Path(directory), endpoint, marker)
            if not errors:
                return result
        except Exception:
            if not errors:
                raise
        raise ArchiveReadError(f"GitHub fixture read failed: {errors[0]}") from None


async def project_witness(root: Path, endpoint: str, marker: str) -> dict[str, int]:
    data = root / "data"
    data.mkdir(mode=0o700)
    config = root / "archive.toml"
    base = f'''data_dir = "{data}"
host = "127.0.0.1"
port = 0
tick_seconds = 3600

[projects.{PROJECT}]
watchers = []
[projects.{PROJECT}.workers.archive]
'''
    config.write_text(base, encoding="utf-8")
    async with daemon(config) as ledger:
        await ledger.execute(
            "orchestrator",
            "lane_start",
            "archive-lane-start",
            {
                "lane": "archive-lane",
                "worker": "archive",
                "branch": MERGED_BRANCH,
                "work_items": [],
            },
        )
        assert await ledger.fetch_cursor(REPO, PULL_REQUESTS) is None

    marker_file = root / "loopback-marker"
    marker_file.write_text(marker + "\n", encoding="utf-8")
    marker_file.chmod(0o600)
    config.write_text(
        base
        + f'''\n[projects.{PROJECT}.github]
repository = "{REPO}"
api_url = "{endpoint}"
token_file = "{marker_file.name}"
poll_seconds = 3
image_jobs = ["Fixture image build"]
controller_merges = false
''',
        encoding="utf-8",
    )

    done: asyncio.Queue[None] = asyncio.Queue()
    original_poll = GitHubDetailPoller.poll

    async def observe_completed(self: GitHubDetailPoller, client: httpx.AsyncClient) -> None:
        await original_poll(self, client)
        done.put_nowait(None)

    def count_actions() -> int:
        with closing(sqlite3.connect(data / f"{PROJECT}.db")) as database:
            return int(database.execute("SELECT count(*) FROM actions").fetchone()[0])

    with patch.object(GitHubDetailPoller, "poll", observe_completed):
        async with daemon(config) as ledger, asyncio.timeout(85):
            await done.get()
            cursor = await ledger.fetch_cursor(REPO, PULL_REQUESTS)
            assert cursor is not None
            assert cursor.generation == 1
            await verify_projection(ledger)
            events = await ledger.events_after(0, 500)
            assert len(events) < 500
            verify_events(events)
            assert await asyncio.to_thread(count_actions) == 0
            # Closed PR details are normally skipped once reconciled. Make only
            # this disposable cursor stale so identical checks and thread are read again.
            previous = await detail_generations(ledger)
            detail_cursor = await ledger.fetch_cursor(REPO, "pull_details/2")
            assert detail_cursor is not None
            await ledger.run(
                "system",
                lambda uow: uow.save_fetch_cursor(
                    REPO, "pull_details/2", replace(detail_cursor, watermark="")
                ),
            )
            await done.get()
            current = await ledger.fetch_cursor(REPO, PULL_REQUESTS)
            assert current is not None
            assert current.generation == cursor.generation + 1
            await verify_projection(ledger)
            assert await detail_generations(ledger) == (previous[0] + 1, previous[1] + 1)
            assert await ledger.events_after(0, 500) == events
            assert await asyncio.to_thread(count_actions) == 0
    return {"events": len(events), "actions": 0, "cursor_generation": current.generation}


async def detail_generations(ledger: Ledger) -> tuple[int, int]:
    checks = await ledger.github_resource(REPO, CHECKS, REVIEW_HEAD)
    threads = await ledger.github_resource(REPO, THREADS, "2")
    assert checks is not None
    assert threads is not None
    return checks.generation, threads.generation


async def verify_projection(ledger: Ledger) -> None:
    merged = await ledger.pull_request(REPO, 1)
    reviewed = await ledger.pull_request(REPO, 2)
    assert merged is not None
    assert reviewed is not None
    assert (
        merged.repository,
        merged.head_repository,
        merged.branch,
        merged.head_sha,
        merged.merge_commit_sha,
        merged.state,
    ) == (
        REPO,
        REPO,
        MERGED_BRANCH,
        MERGED_HEAD,
        MERGED_COMMIT,
        "merged",
    )
    assert (
        reviewed.repository,
        reviewed.head_repository,
        reviewed.branch,
        reviewed.head_sha,
        reviewed.state,
    ) == (REPO, REPO, REVIEW_BRANCH, REVIEW_HEAD, "closed")
    checks = await ledger.github_resource(REPO, CHECKS, REVIEW_HEAD)
    assert checks is not None
    assert checks.complete
    by_id = {check.fields["id"]: check for check in checks.records}
    for check_id, name in CHECK_NAMES.items():
        row = by_id[check_id]
        assert (
            row.fields["name"],
            row.fields["head_sha"],
            row.fields["status"],
            row.fields["conclusion"],
        ) == (name, REVIEW_HEAD, "completed", "success")
        assert (
            row.details["run_url"] == f"https://github.com/{REPO}/actions/runs/{RUN}/job/{check_id}"
        )
    threads = await ledger.github_resource(REPO, THREADS, "2")
    assert threads is not None
    assert threads.complete
    assert threads.attributes["head_sha"] == REVIEW_HEAD
    (thread,) = threads.records
    assert thread.key == THREAD
    assert thread.fields["resolved"] is True


def verify_events(events: list[Event]) -> None:
    (merged,) = [e for e in events if e.kind == "github.pr_merged" and e.fields.get("number") == 1]
    assert merged.lane_id == "archive-lane"
    assert (merged.fields["repository"], merged.fields["branch"], merged.fields["head_sha"]) == (
        REPO,
        MERGED_BRANCH,
        MERGED_HEAD,
    )
    for check_id in CHECK_NAMES:
        (event,) = [
            e for e in events if e.kind == "github.check_run" and e.fields.get("id") == check_id
        ]
        assert (
            event.fields["repository"],
            event.fields["head_sha"],
            event.fields["status"],
            event.fields["conclusion"],
        ) == (REPO, REVIEW_HEAD, "completed", "success")
    (thread,) = [
        e for e in events if e.kind == "github.review_thread" and e.fields.get("id") == THREAD
    ]
    assert (thread.fields["repository"], thread.fields["number"], thread.fields["resolved"]) == (
        REPO,
        2,
        True,
    )


class PipeReader:
    def __init__(self) -> None:
        self.lock = threading.Lock()

    def read(self, method: str, path: str, body: bytes) -> Response:
        with self.lock:
            print(json.dumps({"request": [method, path, body.decode("utf-8")]}), flush=True)
            frame = json.loads(sys.stdin.buffer.readline(MAX_FRAME))
            return frame["headers"], base64.b64decode(frame["body"], validate=True)


async def compose_witness(
    tag: str,
    read: Reader,
    *,
    module: str = "deploy.epic_two_archived",
    arguments: tuple[str, ...] = (),
    source_files: tuple[str, ...] = ("deploy/epic_two_archived.py",),
) -> dict[str, object]:
    from deploy.bridge_smoke import ROOT, run

    endpoint = os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None
    if endpoint is None:
        endpoint = run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
        ).strip()
    if not endpoint.startswith("unix://"):
        raise RuntimeError("the Compose replay witness requires a local Docker Unix socket")
    image = run(["docker", "image", "inspect", "--format", "{{.Id}}", f"plotroom:{tag}"]).strip()
    volumes: list[str] = []
    for name in source_files:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("the Compose replay mount needs a repository-relative path")
        source = (ROOT / relative).resolve(strict=True)
        if not source.is_file() or not source.is_relative_to(ROOT):
            raise RuntimeError("the Compose replay mount needs a trusted source file")
        volumes.append(f"{source}:/fixture/{relative.as_posix()}:ro")
    with tempfile.TemporaryDirectory(prefix="plotroom-epic-two-archive-compose-") as directory:
        file = Path(directory) / "compose.json"
        file.write_text(
            json.dumps(
                {
                    "services": {
                        "archive": {
                            "image": image,
                            "network_mode": "none",
                            "read_only": True,
                            "tmpfs": ["/tmp"],  # noqa: S108 - private container tmpfs
                            "working_dir": "/fixture",
                            "volumes": volumes,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        name = f"plotroom-epic-two-archive-{uuid4().hex[:12]}"
        container = name + "-run"
        command = ["docker", "compose", "-p", name, "--env-file", os.devnull, "-f", str(file)]
        identify = [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={name}",
            "--filter",
            f"name=^/{container}$",
        ]
        if (await asyncio.to_thread(run, identify)).strip():
            raise RuntimeError("archived Compose witness name is already in use")
        proc = await asyncio.create_subprocess_exec(
            *command,
            "run",
            "--rm",
            "--name",
            container,
            "--no-deps",
            "-T",
            "--pull",
            "never",
            "--entrypoint",
            "/app/.venv/bin/python",
            "archive",
            "-B",
            "-m",
            module,
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=MAX_FRAME,
        )
        assert proc.stdout is not None
        assert proc.stdin is not None
        try:
            async with asyncio.timeout(100):
                for frame_number in range(150):
                    line = await proc.stdout.readline()
                    if not line:
                        raise RuntimeError("archived Compose witness stopped without a result")
                    if frame_number == 0:
                        identity = await asyncio.to_thread(
                            run,
                            [
                                "docker",
                                "inspect",
                                "--format",
                                "{{.Image}} {{.HostConfig.NetworkMode}}",
                                container,
                            ],
                        )
                        if identity.strip() != f"{image} none":
                            raise RuntimeError("Compose did not use the pinned isolated image")
                    frame = json.loads(line)
                    if set(frame) == {"result"}:
                        if await proc.wait():
                            raise RuntimeError("archived Compose witness exited unsuccessfully")
                        return frame["result"]
                    method, path, body = frame["request"]
                    headers, payload = await asyncio.to_thread(
                        read, method, path, body.encode("utf-8")
                    )
                    proc.stdin.write(
                        json.dumps(
                            {"headers": headers, "body": base64.b64encode(payload).decode("ascii")}
                        ).encode("utf-8")
                        + b"\n"
                    )
                    await proc.stdin.drain()
            raise RuntimeError("archived Compose witness exhausted its frame budget")
        finally:
            if proc.returncode is None:
                with suppress(ProcessLookupError):
                    proc.kill()
                with suppress(TimeoutError):
                    async with asyncio.timeout(5):
                        await proc.communicate()
            owned = (await asyncio.to_thread(run, identify)).split()
            if owned:
                await asyncio.to_thread(run, ["docker", "rm", "--force", *owned])
            await asyncio.to_thread(run, [*command, "down", "--volumes"])


async def archived_checks(
    tag: str, report: Callable[[str], None], *, transport: ReadTransport | None = None
) -> None:
    provider = ProviderReads(transport=transport, describe_archive_errors=transport is not None)
    await asyncio.to_thread(provider.preflight)
    local = await witness(provider.read)
    report(f"Local archived provider facts PASS: {local}; {provider.live} live GitHub reads.")
    replayed = await compose_witness(tag, provider.replay)
    assert local == replayed
    report(f"Compose archived provider facts PASS: {replayed}; same captured GitHub inputs.")


if __name__ == "__main__":
    print(json.dumps({"result": asyncio.run(witness(PipeReader().read))}), flush=True)
