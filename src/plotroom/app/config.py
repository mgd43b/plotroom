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
