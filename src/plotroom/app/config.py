"""Configuration: one TOML file plus ``PLOTROOM_*`` environment overrides, and the project registry.

Tool credentials (AD-12) are never read here; they are named ``PLOTROOM_<PROJECT>_<NAME>`` and
read by the adapter that needs them, so the override names below are a closed list.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from plotroom.adapters.claude_desktop import SESSION_ID
from plotroom.adapters.github import RECONCILE_EVERY, github_secret_name
from plotroom.adapters.github.ticket_tree import validate_tree_path
from plotroom.core.merge_gate import GatePolicy
from plotroom.core.roles import role_family
from plotroom.core.snapshot import DEFAULT_SNAPSHOT_BUDGET, MIN_BOARD_BUDGET
from plotroom.core.trigger_actions import DeliveryRoute

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_ALLOWED_HOSTS = ("localhost:*", "127.0.0.1:*", "[::1]:*")
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUPS = 5

_WILDCARD_TO_LOOPBACK = {"0.0.0.0": "127.0.0.1", "::": "::1"}  # noqa: S104
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")
_WORKFLOW = re.compile(r"[A-Za-z0-9_.-]+\.ya?ml|[1-9][0-9]{0,18}")
_GITHUB_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
GITHUB_API = "https://api.github.com"
_TOP_LEVEL_KEYS = {
    "data_dir",
    "host",
    "port",
    "allowed_hosts",
    "projects",
    "log_max_bytes",
    "log_backups",
    "snapshot_budget",
    "next_budget",
    "tick_seconds",
    "action_attempts",
    "wait_cap",
}


class ConfigError(Exception):
    """The configuration is unusable; the message says which setting and why."""


@dataclass(frozen=True)
class SwitchboardConfig:
    board: str
    command: tuple[str, ...]
    senders: Mapping[str, str]


@dataclass(frozen=True)
class SessionReaderConfig:
    claude_home: Path
    app_support: Path
    sessions: Mapping[str, str]
    poll_seconds: int


@dataclass(frozen=True)
class GitHubConfig:
    repository: str
    token_file: Path | None
    api_url: str
    poll_seconds: int
    image_jobs: tuple[str, ...] = ()
    merge_gate_policy: GatePolicy = field(default_factory=GatePolicy)


@dataclass(frozen=True)
class GitHubIssuesConfig:
    starter: Path
    store_config: Path
    milestone: int | None


@dataclass(frozen=True)
class Config:
    """The validated settings a plotroom process runs with."""

    data_dir: Path
    host: str
    port: int
    allowed_hosts: tuple[str, ...]
    projects: tuple[str, ...]
    log_max_bytes: int = DEFAULT_LOG_MAX_BYTES
    log_backups: int = DEFAULT_LOG_BACKUPS
    drain_sessions: Mapping[str, str] = field(default_factory=lambda: {})

    workers: Mapping[str, frozenset[str]] = field(default_factory=lambda: {})
    ticket_trees: Mapping[str, tuple[Path, Path]] = field(default_factory=lambda: {})
    snapshot_budget: int = DEFAULT_SNAPSHOT_BUDGET
    next_budget: int = DEFAULT_SNAPSHOT_BUDGET
    tick_seconds: int = 60
    action_attempts: int = 3
    wait_cap: int = 240
    watchers: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: {})
    switchboards: Mapping[str, SwitchboardConfig] = field(default_factory=lambda: {})
    session_readers: Mapping[str, SessionReaderConfig] = field(default_factory=lambda: {})
    github: Mapping[str, GitHubConfig] = field(default_factory=lambda: {})

    github_ticket_trees: Mapping[str, tuple[str, Path]] = field(default_factory=lambda: {})
    github_issue_sources: Mapping[str, GitHubIssuesConfig] = field(default_factory=lambda: {})
    delivery_routes: Mapping[str, Mapping[str, DeliveryRoute]] = field(default_factory=lambda: {})

    @property
    def url(self) -> str:
        """Where a local client reaches the server's MCP endpoint."""
        host = _WILDCARD_TO_LOOPBACK.get(self.host, self.host)
        host = f"[{host}]" if ":" in host else host
        return f"http://{host}:{self.port}/mcp"


def default_config_path(env: Mapping[str, str]) -> Path:
    """``PLOTROOM_CONFIG``, else ``~/.config/plotroom/config.toml``."""
    if explicit := env.get("PLOTROOM_CONFIG"):
        return Path(explicit).expanduser()
    return Path.home() / ".config" / "plotroom" / "config.toml"


def default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "plotroom"


def load_config(
    path: Path | None,
    env: Mapping[str, str],
    *,
    require_projects: bool = True,
) -> Config:
    """Load the file at ``path`` (or the default location, if any), then apply the environment.

    A file named explicitly, by ``path`` or ``PLOTROOM_CONFIG``, must exist; the default location
    may be absent, which leaves environment and defaults to decide.
    """
    explicit = path is not None or "PLOTROOM_CONFIG" in env
    file_path = (path if path is not None else default_config_path(env)).resolve()
    raw: dict[str, Any] = {}
    base = Path.cwd()
    if file_path.exists():
        try:
            raw = tomllib.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read config {file_path}: {exc}") from exc
        base = file_path.parent
    elif explicit:
        raise ConfigError(f"config file {file_path} does not exist")

    unknown = sorted(set(raw) - _TOP_LEVEL_KEYS)
    if unknown:
        raise ConfigError(f"unknown config keys in {file_path}: {', '.join(unknown)}")

    data_dir = _data_dir(env.get("PLOTROOM_DATA_DIR"), raw.get("data_dir"), base).resolve()
    host = _string("host", env.get("PLOTROOM_HOST", raw.get("host", DEFAULT_HOST)))
    port = _port(env.get("PLOTROOM_PORT", raw.get("port", DEFAULT_PORT)))
    allowed_hosts = _string_list(
        "allowed_hosts",
        env.get("PLOTROOM_ALLOWED_HOSTS", raw.get("allowed_hosts")),
        DEFAULT_ALLOWED_HOSTS,
    )
    projects = _projects(env.get("PLOTROOM_PROJECTS"), raw.get("projects"))
    drain_sessions = _drain_sessions(projects, raw.get("projects"))
    workers = _workers(projects, raw.get("projects"))
    switchboards = _switchboards(projects, raw.get("projects"))
    worker_sessions = _worker_sessions(projects, raw.get("projects"), drain_sessions)
    if require_projects and not projects:
        raise ConfigError(
            "no projects configured: add a [projects.<id>] table to the config file "
            "or set PLOTROOM_PROJECTS to a comma-separated list of project ids"
        )
    log_max_bytes = _integer(
        "log_max_bytes",
        env.get("PLOTROOM_LOG_MAX_BYTES", raw.get("log_max_bytes", DEFAULT_LOG_MAX_BYTES)),
        minimum=1,
    )
    log_backups = _integer(
        "log_backups",
        env.get("PLOTROOM_LOG_BACKUPS", raw.get("log_backups", DEFAULT_LOG_BACKUPS)),
        minimum=0,
    )
    github = _github_projects(projects, raw.get("projects"), base)
    github_trees, github_issues = _github_work_items(projects, raw.get("projects"), base, github)
    return Config(
        data_dir=data_dir,
        host=host,
        port=port,
        allowed_hosts=allowed_hosts,
        projects=projects,
        log_max_bytes=log_max_bytes,
        log_backups=log_backups,
        drain_sessions=drain_sessions,
        workers=workers,
        ticket_trees=_ticket_trees(projects, raw.get("projects"), base),
        watchers=_watchers(projects, raw.get("projects"), drain_sessions),
        switchboards=switchboards,
        session_readers=_session_readers(projects, raw.get("projects"), base, worker_sessions),
        github=github,
        github_ticket_trees=github_trees,
        github_issue_sources=github_issues,
        delivery_routes=_delivery_routes(projects, switchboards, worker_sessions, drain_sessions),
        wait_cap=_integer("wait_cap", raw.get("wait_cap", 240), minimum=1),
        snapshot_budget=_integer(
            "snapshot_budget",
            raw.get("snapshot_budget", DEFAULT_SNAPSHOT_BUDGET),
            minimum=MIN_BOARD_BUDGET,
        ),
        next_budget=_integer(
            "next_budget", raw.get("next_budget", DEFAULT_SNAPSHOT_BUDGET), minimum=MIN_BOARD_BUDGET
        ),
        tick_seconds=_integer("tick_seconds", raw.get("tick_seconds", 60), minimum=1),
        action_attempts=_integer("action_attempts", raw.get("action_attempts", 3), minimum=1),
    )


def _data_dir(env_value: str | None, file_value: Any, base: Path) -> Path:
    """The environment's path is relative to the process; the file's is relative to the file."""
    if env_value is not None:
        return Path(_string("PLOTROOM_DATA_DIR", env_value)).expanduser()
    if file_value is None:
        return default_data_dir()
    candidate = Path(_string("data_dir", file_value)).expanduser()
    return candidate if candidate.is_absolute() else (base / candidate)


def _string(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _port(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ConfigError(f"port must be an integer between 0 and 65535, got {value!r}")
    try:
        port = int(value)
    except ValueError as exc:
        raise ConfigError(f"port must be an integer between 0 and 65535, got {value!r}") from exc
    if not 0 <= port <= 65535:
        raise ConfigError(f"port must be an integer between 0 and 65535, got {port}")
    return port


def _integer(name: str, value: Any, *, minimum: int) -> int:
    message = f"{name} must be an integer of at least {minimum}, got {value!r}"
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ConfigError(message)
    try:
        number = int(value)
    except ValueError as exc:
        raise ConfigError(message) from exc
    if number < minimum:
        raise ConfigError(message)
    return number


def _string_list(name: str, value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    items: Any = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(i, str) for i in cast("list[Any]", items)):
        raise ConfigError(f"{name} must be a list of strings")
    cleaned = tuple(str(i).strip() for i in cast("list[Any]", items) if str(i).strip())
    if not cleaned:
        raise ConfigError(f"{name} must not be empty")
    return cleaned


def _projects(env_value: str | None, table: Any) -> tuple[str, ...]:
    ids: list[str]
    if env_value is not None:
        ids = [part.strip() for part in env_value.split(",") if part.strip()]
    elif table is None:
        ids = []
    elif isinstance(table, dict):
        ids = [str(key) for key in cast("dict[Any, Any]", table)]
    else:
        raise ConfigError("projects must be a table of [projects.<id>] entries")
    for project_id in ids:
        if not _SLUG.fullmatch(project_id):
            raise ConfigError(
                f"project id {project_id!r} must be a lowercase slug (letters, digits, '-' or '_')"
            )
    if len(set(ids)) != len(ids):
        raise ConfigError("project ids must be unique")
    return tuple(ids)


def _drain_sessions(projects: tuple[str, ...], table: Any) -> dict[str, str]:
    if not isinstance(table, dict):
        return {}
    entries = cast("dict[str, Any]", table)
    sessions: dict[str, str] = {}
    for project_id in projects:
        settings: Any = entries.get(project_id, {})
        if not isinstance(settings, dict):
            raise ConfigError(f"projects.{project_id} must be a table")
        project = cast("dict[str, Any]", settings)
        if "drain_session" in project:
            sessions[project_id] = _string(
                f"projects.{project_id}.drain_session", project["drain_session"]
            )
    return sessions


def _workers(projects: tuple[str, ...], table: Any) -> dict[str, frozenset[str]]:
    configured: dict[str, frozenset[str]] = {}
    if not isinstance(table, dict):
        return configured

    entries = cast("dict[str, dict[str, Any]]", table)
    for project_id in projects:
        workers: Any = entries.get(project_id, {}).get("workers", {})
        if not isinstance(workers, dict):
            raise ConfigError(f"projects.{project_id}.workers must be a table keyed by worker name")
        names = cast("dict[str, Any]", workers)
        for name, value in names.items():
            if not isinstance(value, dict):
                raise ConfigError(f"projects.{project_id}.workers.{name} must be a table")
            try:
                role_family(f"worker:{name}")
            except ValueError as exc:
                raise ConfigError(
                    f"projects.{project_id}.workers.{name}: invalid worker name"
                ) from exc
        configured[project_id] = frozenset(names)
    return configured


def _switchboards(projects: tuple[str, ...], table: Any) -> dict[str, SwitchboardConfig]:
    entries = cast("dict[str, dict[str, Any]]", table if isinstance(table, dict) else {})
    configured: dict[str, SwitchboardConfig] = {}
    boards: set[str] = set()
    for project_id in projects:
        project = entries.get(project_id, {})
        if "switchboard" not in project:
            continue
        label = f"projects.{project_id}.switchboard"
        raw_settings = project["switchboard"]
        if not isinstance(raw_settings, dict):
            raise ConfigError(f"{label} must be a table")
        settings = cast("dict[str, Any]", raw_settings)
        board = _string(f"{label}.board", settings.get("board"))
        if (
            not _SLUG.fullmatch(board)
            or board.endswith(("-", "_"))
            or board == "project"
            or board in boards
        ):
            raise ConfigError(f"{label}.board must be a unique canonical relay name")
        boards.add(board)
        command = _string_list(f"{label}.command", settings.get("command"), ("switchboard-relay",))
        senders: dict[str, str] = {}
        for worker, worker_settings in project.get("workers", {}).items():
            if "switchboard_address" in worker_settings:
                address = _string(
                    f"{label} address for {worker}", worker_settings["switchboard_address"]
                )
                if address in senders:
                    raise ConfigError(f"{label} worker addresses must be unique")
                senders[address] = worker
        configured[project_id] = SwitchboardConfig(board, command, senders)
    return configured


def _session_readers(
    projects: tuple[str, ...], table: Any, base: Path, sessions: Mapping[str, dict[str, str]]
) -> dict[str, SessionReaderConfig]:
    entries = cast("dict[str, dict[str, Any]]", table if isinstance(table, dict) else {})
    configured: dict[str, SessionReaderConfig] = {}
    for project_id in projects:
        project = entries.get(project_id, {})
        if "session_reader" not in project:
            continue
        label = f"projects.{project_id}.session_reader"
        raw_settings = project["session_reader"]
        if not isinstance(raw_settings, dict):
            raise ConfigError(f"{label} must be a table")
        settings = cast("dict[str, Any]", raw_settings)
        home = (
            base / Path(_string(f"{label}.claude_home", settings.get("claude_home"))).expanduser()
        )
        support = (
            base / Path(_string(f"{label}.app_support", settings.get("app_support"))).expanduser()
        )
        interval = _integer(f"{label}.poll_seconds", settings.get("poll_seconds", 1), minimum=1)
        configured[project_id] = SessionReaderConfig(
            home.resolve(), support.resolve(), sessions[project_id], interval
        )
    return configured


def _worker_sessions(
    projects: tuple[str, ...], table: Any, drains: Mapping[str, str]
) -> dict[str, dict[str, str]]:
    entries = cast("dict[str, dict[str, Any]]", table if isinstance(table, dict) else {})
    configured: dict[str, dict[str, str]] = {}
    for project_id in projects:
        sessions: dict[str, str] = {}
        project = entries.get(project_id, {})
        if "session_reader" not in project and project_id not in drains:
            continue
        for worker, settings in project.get("workers", {}).items():
            if "claude_session" not in settings:
                continue
            label = f"projects.{project_id}.workers.{worker}.claude_session"
            session = _string(label, settings["claude_session"])
            if not SESSION_ID.fullmatch(session) or session in sessions:
                raise ConfigError(f"{label}: Claude session IDs must be valid and unique")
            sessions[session] = worker
        configured[project_id] = sessions
    return configured


def _delivery_routes(
    projects: tuple[str, ...],
    switchboards: Mapping[str, SwitchboardConfig],
    sessions: Mapping[str, dict[str, str]],
    drains: Mapping[str, str],
) -> dict[str, Mapping[str, DeliveryRoute]]:
    configured: dict[str, Mapping[str, DeliveryRoute]] = {}
    for project_id in projects:
        relay = switchboards.get(project_id)
        routes = {
            worker: DeliveryRoute("switchboard", address)
            for address, worker in (relay.senders.items() if relay is not None else ())
        }
        if project_id in drains:
            for session, worker in sessions[project_id].items():
                # An explicit relay address takes precedence over a session reader mapping.
                routes.setdefault(worker, DeliveryRoute("claude", session))
        configured[project_id] = routes
    return configured


def _github_projects(projects: tuple[str, ...], table: Any, base: Path) -> dict[str, GitHubConfig]:
    entries = cast("dict[str, dict[str, Any]]", table if isinstance(table, dict) else {})
    configured: dict[str, GitHubConfig] = {}
    names: set[str] = set()
    for project_id in projects:
        project = entries.get(project_id, {})
        if "github" not in project:
            continue
        label = f"projects.{project_id}.github"
        raw_settings = project["github"]
        if not isinstance(raw_settings, dict):
            raise ConfigError(f"{label} must be a table")
        settings = cast("dict[str, Any]", raw_settings)
        repository = _string(f"{label}.repository", settings.get("repository")).lower()
        if not _REPOSITORY.fullmatch(repository) or repository.split("/")[1] in (".", ".."):
            raise ConfigError(f"{label}.repository must be a GitHub owner/repository")
        name = github_secret_name(project_id)
        if name in names:
            raise ConfigError(f"{label} credential environment variable must be unique")
        names.add(name)
        token_file = None
        if "token_file" in settings:
            token_file = (
                base / Path(_string(f"{label}.token_file", settings["token_file"])).expanduser()
            )
        api_url = _string(f"{label}.api_url", settings.get("api_url", GITHUB_API)).rstrip("/")
        if api_url != GITHUB_API:
            try:
                url = urlsplit(api_url)
                local = (
                    url.scheme == "http"
                    and url.hostname in ("127.0.0.1", "::1")
                    and url.port is not None
                    and url.username is None
                    and not (url.path or url.query or url.fragment)
                )
            except ValueError:
                local = False
            if not local:
                raise ConfigError(f"{label}.api_url must be the GitHub API or a loopback fixture")
        poll_seconds = _integer(
            f"{label}.poll_seconds", settings.get("poll_seconds", 60), minimum=1
        )
        configured[project_id] = GitHubConfig(
            repository,
            token_file,
            api_url,
            poll_seconds,
            _string_list(f"{label}.image_jobs", settings.get("image_jobs"), ()),
            _merge_gate_policy(label, settings, poll_seconds),
        )
    return configured


def _merge_gate_policy(label: str, settings: dict[str, Any], poll_seconds: int) -> GatePolicy:
    minimum = (
        _integer(f"{label}.min_checks", settings["min_checks"], minimum=1)
        if "min_checks" in settings
        else None
    )
    ignored = settings.get("ignore_checks", [])
    if not isinstance(ignored, list) or not all(
        isinstance(name, str) and name for name in cast("list[Any]", ignored)
    ):
        raise ConfigError(f"{label}.ignore_checks must be a list of nonempty check names")
    workflow = None
    if "ci_workflow" in settings:
        raw_workflow = settings["ci_workflow"]
        workflow = _string(
            f"{label}.ci_workflow", str(raw_workflow) if type(raw_workflow) is int else raw_workflow
        )
        if not _WORKFLOW.fullmatch(workflow) or (
            workflow.isdecimal() and int(workflow) > 2**63 - 1
        ):
            raise ConfigError(f"{label}.ci_workflow must be a workflow filename or positive ID")
    fix_label = (
        _string(f"{label}.fix_label", settings["fix_label"]) if "fix_label" in settings else None
    )
    raw_logins = settings.get("authorized_label_logins", [])
    if not isinstance(raw_logins, list) or not all(
        isinstance(login, str) and _GITHUB_LOGIN.fullmatch(login)
        for login in cast("list[Any]", raw_logins)
    ):
        raise ConfigError(f"{label}.authorized_label_logins must be a list of GitHub logins")
    controller_merges = settings.get("controller_merges", False)
    if type(controller_merges) is not bool:
        raise ConfigError(f"{label}.controller_merges must be a Boolean")
    merge_method = settings.get("merge_method", "merge")
    if merge_method not in ("merge", "squash", "rebase"):
        raise ConfigError(f"{label}.merge_method must be merge, squash, or rebase")
    skip_label = (
        _string(f"{label}.skip_label", settings["skip_label"]) if "skip_label" in settings else None
    )
    try:
        return GatePolicy(
            minimum,
            tuple(cast("list[str]", ignored)),
            max_age=timedelta(seconds=2 * poll_seconds),
            pull_max_age=timedelta(seconds=(RECONCILE_EVERY + 2) * poll_seconds),
            ci_workflow=workflow,
            fix_label=fix_label,
            authorized_label_logins=tuple(login.lower() for login in cast("list[str]", raw_logins)),
            skip_label=skip_label,
            merge_method=merge_method,
            controller_merges=controller_merges,
        )
    except OverflowError:
        raise ConfigError(f"{label}.poll_seconds is too large for the merge gate") from None


def _watchers(
    projects: tuple[str, ...], table: Any, drain_sessions: Mapping[str, str]
) -> dict[str, tuple[str, ...]]:
    entries = cast("dict[str, dict[str, Any]]", table if isinstance(table, dict) else {})
    configured: dict[str, tuple[str, ...]] = {}
    for project_id in projects:
        value: Any = entries.get(project_id, {}).get("watchers")
        if value is None:
            configured[project_id] = (
                ("orchestrator", "clerk", "drain")
                if project_id in drain_sessions
                else ("orchestrator", "clerk")
            )
            continue
        label = f"projects.{project_id}.watchers"
        if value == []:
            configured[project_id] = ()
            continue
        roles = _string_list(label, value, ())
        try:
            if any(role_family(role) == "system" for role in roles):
                raise ValueError("system cannot watch")
        except ValueError as exc:
            raise ConfigError(f"{label}: {exc}") from None
        configured[project_id] = tuple(dict.fromkeys(roles))
    return configured


def _ticket_trees(
    projects: tuple[str, ...], table: Any, base: Path
) -> dict[str, tuple[Path, Path]]:
    sources: dict[str, tuple[Path, Path]] = {}
    if not isinstance(table, dict):
        return sources
    entries = cast("dict[str, dict[str, Any]]", table)
    for project_id in projects:
        settings = entries.get(project_id, {})
        if "ticket_tree" in settings:
            field_name = f"projects.{project_id}"
            tree = (
                base
                / Path(_string(f"{field_name}.ticket_tree", settings["ticket_tree"])).expanduser()
            ).resolve()
            if "bmad_installation" not in settings:
                raise ConfigError(
                    f"{field_name}.bmad_installation must name a trusted BMad installation"
                )
            installation = (
                base
                / Path(
                    _string(f"{field_name}.bmad_installation", settings["bmad_installation"])
                ).expanduser()
            ).resolve()
            sources[project_id] = tree, installation
    return sources


def _github_work_items(
    projects: tuple[str, ...], table: Any, base: Path, github: Mapping[str, GitHubConfig]
) -> tuple[dict[str, tuple[str, Path]], dict[str, GitHubIssuesConfig]]:
    entries = cast("dict[str, dict[str, Any]]", table if isinstance(table, dict) else {})
    trees: dict[str, tuple[str, Path]] = {}
    issues: dict[str, GitHubIssuesConfig] = {}
    for project_id in projects:
        project = entries.get(project_id, {})
        configured = project.keys() & {"ticket_tree", "github_ticket_tree", "github_issues"}
        label = f"projects.{project_id}"
        if len(configured) > 1:
            raise ConfigError(f"{label} must select one work-item source")
        if not configured or "ticket_tree" in configured:
            continue
        if project_id not in github:
            raise ConfigError(f"{label} needs GitHub configuration for its work-item source")
        installation = (
            base
            / Path(
                _string(f"{label}.bmad_installation", project.get("bmad_installation"))
            ).expanduser()
        ).resolve()
        if "github_ticket_tree" in configured:
            tree = _string(f"{label}.github_ticket_tree", project["github_ticket_tree"])
            try:
                validate_tree_path(tree)
            except ValueError:
                raise ConfigError(
                    f"{label}.github_ticket_tree must be a repository-relative path"
                ) from None
            trees[project_id] = tree, installation
            continue
        settings = project["github_issues"]
        if not isinstance(settings, dict):
            raise ConfigError(f"{label}.github_issues must be a table")
        settings = cast("dict[str, Any]", settings)
        if settings.keys() - {"store_config", "milestone"}:
            raise ConfigError(f"{label}.github_issues supports store_config and milestone")
        store = (
            base
            / Path(
                _string(f"{label}.github_issues.store_config", settings.get("store_config"))
            ).expanduser()
        ).resolve()
        milestone = (
            _integer(f"{label}.github_issues.milestone", settings["milestone"], minimum=1)
            if "milestone" in settings
            else None
        )
        starter = installation / ".agents/skills/bmad-agent-po/config/github-ticketing.toml"
        issues[project_id] = GitHubIssuesConfig(starter, store, milestone)
    return trees, issues
