"""``plotroom serve``: take the lock, check the floor, migrate, serve (AD-3, AD-4, AD-5, AD-7)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import urlsplit

import httpx
import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from plotroom.adapters.claude_desktop import ClaudeDesktopReader
from plotroom.adapters.embedding_http import HttpEmbedder
from plotroom.adapters.github import (
    GitHubPoller,
    LocalGitHubCredential,
    PullRequestFetcher,
    github_secret_name,
)
from plotroom.adapters.github.details import GitHubDetailPoller
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.adapters.github.ticket_tree import GitHubTicketTree
from plotroom.adapters.mcp import build_app
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.store_sqlite.store import retryable_search_write_error
from plotroom.adapters.switchboard import Switchboard
from plotroom.adapters.work_items_bmad import BmadTicketTree, read_ticket_body, read_ticket_tree
from plotroom.adapters.work_items_github_issues import GitHubIssuesWorkItems
from plotroom.app.clock import SystemClock
from plotroom.app.config import Config, ConfigError
from plotroom.app.lock import DataDirLock
from plotroom.app.portfile import (
    DirectoryIdentity,
    directory_identity,
    directory_key,
    pin_directory,
    prepare_local_socket,
    published_socket,
    read_listener,
    remove_local_socket,
    remove_port,
    write_port,
)
from plotroom.app.runtime import ProjectRuntime
from plotroom.app.session_intake import SessionIntake
from plotroom.app.sqlite_floor import floor_problem
from plotroom.app.tokens import TokenRegistry
from plotroom.core.actions import SWITCHBOARD_SEND
from plotroom.core.ask_checks import DEFAULT_ASK_STALE_DAYS
from plotroom.core.catalog import build_catalog
from plotroom.core.ledger import Ledger
from plotroom.core.routing import seed_routes
from plotroom.core.search import SemanticSearch, backfill_search, follow_search_embeddings
from plotroom.ports import Clock
from plotroom.ports.github_write import FixtureGitHubMerge
from plotroom.ports.usage_meter import UsageMeter
from plotroom.ports.work_items import WorkItemSource

log = logging.getLogger("plotroom.serve")


def _require_directory(data_dir: Path, identity: DirectoryIdentity) -> None:
    try:
        if directory_identity(data_dir) == identity:
            return
    except OSError:
        pass
    raise ConfigError("the canonical plotroom data directory changed during startup")


class _Server(uvicorn.Server):
    """uvicorn's server that publishes the address it actually bound."""

    def __init__(
        self,
        config: uvicorn.Config,
        started: asyncio.Future[int],
        data_dir: Path,
        directory_fd: int,
        on_shutdown: Callable[[], None],
    ) -> None:
        super().__init__(config)
        self._started_future = started
        self._on_shutdown = on_shutdown
        self._data_dir = data_dir
        self._directory_fd = directory_fd
        self._identity = directory_identity(directory_fd)
        self._local_socket: Path | None = None
        self._local_listener: asyncio.Server | None = None

    _shutdown_task: asyncio.Task[None] | None = None

    def shutdown_task(self, sockets: Any = None) -> asyncio.Task[None]:
        """The one task that shuts uvicorn down, started on first use by whoever needs it."""
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.ensure_future(self._shut_down(sockets))
        return self._shutdown_task

    async def _shut_down(self, sockets: Any) -> None:
        # Stop publishing the listener as soon as it starts shutting down.
        self._on_shutdown()
        self._stop_local_socket()
        with contextlib.suppress(OSError):
            remove_port(self._data_dir, directory_fd=self._directory_fd)
        await uvicorn.Server.shutdown(self, sockets)

    async def shutdown(self, sockets: Any = None) -> None:
        # Shielded: a cancel must not abort a shutdown that is draining requests and lifespan
        await asyncio.shield(self.shutdown_task(sockets))

    async def startup(self, sockets: Any = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and not self._started_future.done():
            bound: Any = self.servers[0].sockets[0].getsockname()
            host = str(bound[0])
            port = int(bound[1])
            # Published before anyone is told the server is up: a client that sees "serving" in
            # the log, or reads the file, finds the same port.
            try:
                _require_directory(self._data_dir, self._identity)
                local_socket = await self._start_local_socket()
                _require_directory(self._data_dir, self._identity)
                write_port(
                    self._data_dir,
                    port,
                    host=host,
                    local_socket=local_socket,
                    local_unavailable=local_socket is None,
                    directory_fd=self._directory_fd,
                )
            except BaseException:
                # uvicorn skips its own shutdown when startup raises, which would leave the
                # listener open after the lock is released.
                await _finish(self.shutdown_task())
                raise
            self._started_future.set_result(port)

    async def _start_local_socket(self) -> Path | None:
        loop = asyncio.get_running_loop()

        def protocol() -> asyncio.Protocol:
            # Use the same app, lifespan, and tracked connections as the TCP listener.
            return H11Protocol(
                config=self.config,
                server_state=self.server_state,
                app_state=self.lifespan.state,
                _loop=loop,
            )

        try:
            local_socket = prepare_local_socket(self._data_dir, directory_fd=self._directory_fd)
            self._local_socket = local_socket
            _require_directory(self._data_dir, self._identity)
            listener = await loop.create_unix_server(
                protocol, path=local_socket, start_serving=False
            )
            self._local_listener = listener
            self.servers.append(listener)  # shutdown owns the socket before it starts accepting
            await listener.start_serving()
        except OSError as exc:
            self._stop_local_socket()
            log.warning("private local listener unavailable", extra={"reason": str(exc)})
            return None
        return local_socket

    def _stop_local_socket(self) -> None:
        if self._local_listener is not None:
            self._local_listener.close()
            self._local_listener = None
        if self._local_socket is not None:
            directory_fd = (
                self._directory_fd if self._local_socket.parent.parent == self._data_dir else None
            )
            remove_local_socket(self._local_socket, directory_fd=directory_fd)
            self._local_socket = None


async def _finish(task: asyncio.Task[None]) -> None:
    """Wait for ``task`` to complete, however many times this coroutine is cancelled meanwhile."""
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)
    task.result()


@dataclass
class _Project:
    """A project the server has opened: its store and, once migrated, its running ledger."""

    store: SqliteStore
    ledger: Ledger | None = None
    runtime: ProjectRuntime | None = None
    work_items: WorkItemSource | None = None
    embedding_client: httpx.AsyncClient | None = None


class Daemon:
    """One plotroom server for one data directory."""

    def __init__(
        self,
        config: Config,
        *,
        sqlite_version: str | None = None,
        clock: Clock | None = None,
        expected_directory: str | None = None,
        fixture_github_writes: Mapping[str, FixtureGitHubMerge] | None = None,
        usage_meters: Mapping[str, UsageMeter] | None = None,
    ) -> None:
        self._config = config
        self._usage_meters = dict(usage_meters or {})
        self._fixture_github_writes = dict(fixture_github_writes or {})
        for project_id in self._fixture_github_writes:
            github = config.github.get(project_id)
            url = urlsplit(github.api_url) if github is not None else None
            if url is None or url.scheme != "http" or url.hostname not in ("127.0.0.1", "::1"):
                raise ConfigError("GitHub fixture writes require a loopback fixture project")
        self._expected_directory = expected_directory
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._projects: dict[str, _Project] = {}
        self._sqlite_version = (
            sqlite_version if sqlite_version is not None else sqlite3.sqlite_version
        )
        self._started: asyncio.Future[int] | None = None
        self._server: _Server | None = None
        self._received_sigterm = False

    async def wait_started(self) -> int:
        """The port the server is listening on, once it is."""
        if self._started is None:
            self._started = asyncio.get_running_loop().create_future()
        return await self._started

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True

    async def run_as_service(self) -> None:
        """Serve from the CLI, replaying SIGTERM after releasing the data directory."""
        if threading.current_thread() is not threading.main_thread():
            await self.run()
            return
        # Uvicorn saves this handler, then calls it when replaying SIGTERM after HTTP shutdown.
        # SIGINT still follows Uvicorn's and asyncio's usual handling.
        previous = signal.signal(signal.SIGTERM, self._handle_sigterm)
        try:
            await self.run()
        finally:
            signal.signal(signal.SIGTERM, previous)
            if self._received_sigterm:
                self._received_sigterm = False
                signal.raise_signal(signal.SIGTERM)

    def _handle_sigterm(self, _signal: int, _frame: FrameType | None) -> None:
        self._received_sigterm = True
        self.stop()

    @property
    def ledgers(self) -> dict[str, Ledger]:
        """The running ledger of each opened project (none below the SQLite floor)."""
        return {
            project_id: project.ledger
            for project_id, project in self._projects.items()
            if project.ledger is not None
        }

    async def run(self) -> None:
        """Serve until stopped. Raises ``LockError`` when another server owns the directory."""
        config = self._config
        if self._started is None:
            self._started = asyncio.get_running_loop().create_future()
        with pin_directory(config.data_dir) as directory_fd:
            identity = directory_identity(directory_fd)
            if self._expected_directory is not None and self._expected_directory != directory_key(
                identity
            ):
                raise ConfigError("the canonical plotroom data directory changed during startup")
            with DataDirLock(config.data_dir, directory_fd=directory_fd):
                _require_directory(config.data_dir, identity)
                await self._run_locked(self._started, directory_fd, identity)

    async def _run_locked(
        self, started: asyncio.Future[int], directory_fd: int, identity: DirectoryIdentity
    ) -> None:
        config = self._config
        # Clear a publication left by a previous owner of the directory we locked.
        old = read_listener(config.data_dir, directory_fd=directory_fd)
        remove_port(config.data_dir, directory_fd=directory_fd)
        if old is not None and old.local_socket is not None:
            stale_socket = published_socket(config.data_dir, identity, old.local_socket)
            if stale_socket is not None:
                parent_fd = directory_fd if stale_socket.parent.parent == config.data_dir else None
                remove_local_socket(stale_socket, directory_fd=parent_fd)
        try:
            problem = floor_problem(self._sqlite_version)
            if problem is not None:
                log.error("not ready", extra={"reason": problem})
            else:
                await self._open_projects()
            _require_directory(config.data_dir, identity)
            app = build_app(
                config.projects,
                config.allowed_hosts,
                ledgers=self.ledgers,
                work_item_sources={
                    project_id: project.work_items
                    for project_id, project in self._projects.items()
                    if project.work_items is not None
                },
                authenticate=TokenRegistry(config.data_dir).authenticate,
                unavailable=problem,
                snapshot_budget=config.snapshot_budget,
                next_budget=config.next_budget,
                search_budget=config.search_budget,
            )
            uvicorn_config = uvicorn.Config(
                app,
                host=config.host,
                port=config.port,
                log_config=None,
                access_log=False,
                timeout_graceful_shutdown=5,
            )
            self._server = _Server(
                uvicorn_config,
                started,
                config.data_dir,
                directory_fd,
                on_shutdown=self._stop_waiting,
            )
            if self._received_sigterm:  # opening finished after a stop request
                return
            serving = asyncio.ensure_future(self._announce())
            try:
                await self._server.serve()
            except asyncio.CancelledError:
                # Finish shutdown before releasing the data-directory lock.
                if self._server.started:
                    await _finish(self._server.shutdown_task())
                raise
            finally:
                serving.cancel()
        finally:
            # Unpublish and close the stores before releasing the lock.
            with contextlib.suppress(OSError):
                remove_port(config.data_dir, directory_fd=directory_fd)
            await _finish(asyncio.ensure_future(self._close_projects()))
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise asyncio.CancelledError  # a cancel that arrived mid-teardown still counts

    async def _announce(self) -> None:
        port = await self.wait_started()
        log.info(
            "serving",
            extra={
                "host": self._config.host,
                "port": port,
                "projects": list(self._config.projects),
                "data_dir": str(self._config.data_dir),
            },
        )

    async def _open_projects(self) -> None:
        """Migrate each project file and start its ledger (never reached below the SQLite floor)."""
        tokens = TokenRegistry(self._config.data_dir)
        for project_id in self._config.projects:
            project = _Project(SqliteStore(project_id, self._config.data_dir))
            self._projects[project_id] = project  # registered first so a failure closes it
            await project.store.migrate()
            log.info(
                "migrated",
                extra={"project": project_id, "revision": await project.store.revision()},
            )
            workers = self._config.workers.get(project_id, frozenset())
            source = self._config.ticket_trees.get(project_id)
            work_items: WorkItemSource | None = (
                BmadTicketTree(*source) if source is not None else None
            )
            github = self._config.github.get(project_id)
            desktop = self._config.session_readers.get(project_id)
            repository = github.repository if github is not None else None
            remote_tree = self._config.github_ticket_trees.get(project_id)
            routes = self._config.delivery_routes.get(project_id)
            fixture_write = self._fixture_github_writes.get(project_id)
            policy = github.merge_gate_policy if github is not None else None
            live_merge = policy is not None and policy.ci_workflow is not None
            human_login = self._config.human_logins.get(project_id)
            semantic_search = None
            if (embedder := self._config.embedders.get(project_id)) is not None:
                project.embedding_client = httpx.AsyncClient(trust_env=False)
                semantic_search = SemanticSearch(
                    project.store.embedding_index(embedder.model),
                    HttpEmbedder(project.embedding_client, embedder.url, embedder.model),
                )
            project.ledger = Ledger(
                project.store,
                build_catalog(
                    workers,
                    github_repository=repository,
                    delivery_routes=routes,
                    image_jobs=github.image_jobs if github is not None else (),
                    lane_idle_minutes=self._config.lane_idle_minutes,
                    human_login=human_login,
                ),
                self._clock,
                watcher_roles=self._config.watchers.get(project_id, ()),
                wait_cap=self._config.wait_cap,
                github_repository=repository,
                merge_gate_policy=github.merge_gate_policy if github is not None else None,
                github_tree_path=remote_tree[0] if remote_tree is not None else None,
                session_workers=desktop.sessions if desktop is not None else None,
                drain_enabled=project_id in self._config.drain_sessions,
                brief_settings=self._config.briefs.get(project_id),
                live_merge_controller=live_merge,
                merge_clerk=partial(tokens.has_role, project_id, "clerk") if live_merge else None,
                fixture_merge_enabled=fixture_write is not None,
                fixture_branch_updates=fixture_write is not None,
                human_login=human_login,
                ask_stale_days=self._config.ask_stale_days.get(project_id, DEFAULT_ASK_STALE_DAYS),
                search=project.store,
                semantic_search=semantic_search,
            )
            project.ledger.start()
            if route_seeds := self._config.routes.get(project_id):
                await project.ledger.run("system", partial(seed_routes, routes=route_seeds))
            await project.ledger.initialize_overlaps()
            await project.ledger.clean_disabled_merge_items()
            relay = self._config.switchboards.get(project_id)
            switchboard = (
                Switchboard(project.ledger, relay.board, relay.command, relay.senders)
                if relay is not None
                else None
            )
            inputs = [switchboard.run] if switchboard is not None else []
            inputs.append(
                partial(
                    backfill_search,
                    project.ledger,
                    project.store,
                    retryable_write_error=retryable_search_write_error,
                )
            )
            if semantic_search is not None:
                inputs.append(
                    partial(
                        follow_search_embeddings,
                        project.ledger,
                        semantic_search.index,
                        semantic_search.embedder,
                        retryable_write_error=retryable_search_write_error,
                    )
                )
            if desktop is not None:
                intake = SessionIntake(
                    project.ledger,
                    ClaudeDesktopReader(desktop.claude_home, desktop.app_support),
                    desktop.sessions,
                    desktop.poll_seconds,
                )
                inputs.append(intake.run)
            if github is not None:
                credential = LocalGitHubCredential(
                    github_secret_name(project_id), os.environ, github.token_file
                )
                api = GitHubReadClient(github.api_url, github.repository)
                poller = GitHubPoller(
                    project.ledger,
                    self._clock,
                    github.repository,
                    credential,
                    PullRequestFetcher(github.api_url, github.repository),
                    github.poll_seconds,
                )
                issue_source = self._config.github_issue_sources.get(project_id)
                details = GitHubDetailPoller(
                    project.ledger,
                    self._clock,
                    api,
                    credential,
                    github.image_jobs,
                    include_issue_tree=issue_source is not None,
                    ci_workflow=github.merge_gate_policy.ci_workflow,
                )
                project.ledger.confirm_merge_gate = details.confirm_gate
                inputs.append(partial(poller.run, details.poll))
                if remote_tree is not None:
                    tree, installation = remote_tree
                    work_items = GitHubTicketTree(
                        api,
                        credential,
                        tree,
                        partial(read_ticket_tree, installation),
                        read_body=partial(read_ticket_body, installation),
                        cooling_down=details.cooling_down,
                        report_rate_limit=details.record_rate_limit,
                    )
                if issue_source is not None:
                    work_items = GitHubIssuesWorkItems(
                        details.issue_tree,
                        issue_source.starter,
                        issue_source.store_config,
                        github.repository,
                        issue_source.milestone,
                    )
            project.work_items = work_items
            project.runtime = ProjectRuntime(
                project.ledger,
                self._clock,
                tick_seconds=self._config.tick_seconds,
                action_attempts=self._config.action_attempts,
                handlers={SWITCHBOARD_SEND: switchboard.send} if switchboard is not None else {},
                delivery_routes=routes,
                workers=workers,
                work_items=work_items,
                inputs=tuple(inputs),
                fixture_github_write=fixture_write,
                fixture_github_merge=fixture_write,
                usage_meter=self._usage_meters.get(project_id),
            )
            project.runtime.start()

    def _stop_waiting(self) -> None:
        for ledger in self.ledgers.values():
            ledger.stop_waiting()

    async def _close_projects(self) -> None:
        """Stop every writer (draining queued inputs), then close every store. Never raises."""
        projects = list(self._projects.values())
        for project in projects:
            if project.runtime is not None:
                await project.runtime.stop()
            if project.ledger is not None:
                try:
                    await project.ledger.stop()
                except Exception:
                    log.exception("ledger stop failed", extra={"project": project.store.project_id})
        for project in projects:
            if project.embedding_client is not None:
                try:
                    await project.embedding_client.aclose()
                except Exception:
                    log.exception(
                        "embedding client close failed", extra={"project": project.store.project_id}
                    )
            try:
                await project.store.close()
            except Exception:
                log.exception("store close failed", extra={"project": project.store.project_id})
        self._projects.clear()
