"""Configuration: one TOML file plus ``PLOTROOM_*`` environment overrides, and the project registry.

Tool credentials (AD-12) are never read here; they are named ``PLOTROOM_<PROJECT>_<NAME>`` and
read by the adapter that needs them, so the override names below are a closed list.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from plotroom.core.roles import role_family
from plotroom.core.snapshot import DEFAULT_SNAPSHOT_BUDGET

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_ALLOWED_HOSTS = ("localhost:*", "127.0.0.1:*", "[::1]:*")
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUPS = 5

_WILDCARD_TO_LOOPBACK = {"0.0.0.0": "127.0.0.1", "::": "::1"}  # noqa: S104
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_TOP_LEVEL_KEYS = {
    "data_dir",
    "host",
    "port",
    "allowed_hosts",
    "projects",
    "log_max_bytes",
    "log_backups",
    "snapshot_budget",
    "tick_seconds",
    "action_attempts",
    "wait_cap",
}


class ConfigError(Exception):
    """The configuration is unusable; the message says which setting and why."""


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
    tick_seconds: int = 60
    action_attempts: int = 3
    wait_cap: int = 240
    watchers: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: {})

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
        wait_cap=_integer("wait_cap", raw.get("wait_cap", 240), minimum=1),
        snapshot_budget=_integer(
            "snapshot_budget", raw.get("snapshot_budget", DEFAULT_SNAPSHOT_BUDGET), minimum=1
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
