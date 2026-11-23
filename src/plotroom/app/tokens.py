"""Offline token administration and request-time authentication (AD-8)."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict, cast

from plotroom.core.roles import Identity, validate_token_role

INSTANCE_TOKEN_FILE_NAME = "instance-tokens.json"  # noqa: S105
LOCAL_TOKEN_FILE_NAME = "tokens.json"  # noqa: S105
_ID = re.compile(r"[a-f0-9]{24}")
_BEARER = re.compile(r"([a-f0-9]{24})\.[A-Za-z0-9_-]{43}")
_HASH = re.compile(r"[a-f0-9]{64}")
_PRIVATE_MODE = 0o600


class TokenError(Exception):
    """The credential store or token selection could not be used."""


class LocalCleanupError(TokenError):
    """The instance token is revoked and removing its local copy failed."""

    def __init__(self, failure: TokenError | OSError) -> None:
        super().__init__("token is revoked. local credential cleanup failed")
        self.failure = failure


class _InstanceRecord(TypedDict):
    hash: str
    project: str
    role: str


@dataclass(frozen=True)
class IssuedToken:
    token_id: str
    bearer: str = field(repr=False)


def local_token_path() -> Path:
    return Path.home() / ".config" / "plotroom" / LOCAL_TOKEN_FILE_NAME


def _digest(bearer: str) -> str:
    return hashlib.sha256(bearer.encode("ascii")).hexdigest()


@contextlib.contextmanager
def _locked(path: Path) -> Generator[None]:
    # Atomic replacement changes the data file's inode, so writers lock a separate file.
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, _PRIVATE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read(path: Path) -> dict[str, Any]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise TokenError(f"token file {path} must be a regular file with mode 0600") from exc
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != _PRIVATE_MODE:
            raise TokenError(f"token file {path} must be a regular file with mode 0600")
        with os.fdopen(fd, encoding="utf-8", closefd=False) as handle:
            raw: Any = json.load(handle)
    except ValueError, RecursionError:
        raise TokenError(f"invalid token file {path}") from None
    finally:
        os.close(fd)
    if not isinstance(raw, dict):
        raise TokenError(f"invalid token file {path}")
    return cast("dict[str, Any]", raw)


def _read_instance(path: Path) -> dict[str, _InstanceRecord]:
    return _validate_instance(path, _read(path))


def _validate_instance(path: Path, records: dict[str, Any]) -> dict[str, _InstanceRecord]:
    for token_id, raw in records.items():
        if not _ID.fullmatch(token_id) or not isinstance(raw, dict):
            raise TokenError(f"invalid instance token file {path}")
        record = cast("dict[str, Any]", raw)
        digest, project, role = record.get("hash"), record.get("project"), record.get("role")
        if (
            record.keys() != {"hash", "project", "role"}
            or not isinstance(digest, str)
            or not _HASH.fullmatch(digest)
            or not isinstance(project, str)
            or not project
            or not isinstance(role, str)
        ):
            raise TokenError(f"invalid instance token file {path}")
        try:
            validate_token_role(role)
        except ValueError:
            raise TokenError(f"invalid instance token file {path}") from None
    return cast("dict[str, _InstanceRecord]", records)


def _read_local(path: Path) -> dict[str, dict[str, str]]:
    records = _read(path)
    for raw in records.values():
        if not isinstance(raw, dict):
            raise TokenError(f"invalid local token file {path}")
        for token_id, bearer in cast("dict[str, Any]", raw).items():
            match = _BEARER.fullmatch(bearer) if isinstance(bearer, str) else None
            if match is None or match[1] != token_id:
                raise TokenError(f"invalid local token file {path}")
    return cast("dict[str, dict[str, str]]", records)


def _write(path: Path, records: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), _PRIVATE_MODE)
            json.dump(records, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            Path(temporary).unlink(missing_ok=True)


def select_local_token(data_dir: Path, token_id: str | None) -> str | None:
    """Select the credential keyed by the connection's already-canonical data directory."""
    entries = _read_local(local_token_path()).get(str(data_dir), {})
    if token_id is not None:
        if token_id not in entries:
            raise TokenError("no local token with the selected ID for this data directory")
        return entries[token_id]
    if len(entries) > 1:
        raise TokenError("several local tokens exist for this data directory; use --token-id")
    return next(iter(entries.values()), None)


class TokenRegistry:
    """The authoritative instance file contains only public IDs, hashes, projects and roles."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir.resolve()
        self.path = self._data_dir / INSTANCE_TOKEN_FILE_NAME

    def issue(self, project_id: str, role: str, *, local: bool = True) -> IssuedToken:
        if not project_id:
            raise TokenError("invalid token project ID")
        validate_token_role(role)
        with _locked(self.path):
            records = _read_instance(self.path)
            token_id = secrets.token_hex(12)
            while token_id in records:
                token_id = secrets.token_hex(12)
            bearer = f"{token_id}.{secrets.token_urlsafe(32)}"
            records[token_id] = {"hash": _digest(bearer), "project": project_id, "role": role}
            if local:
                self._save_locally(token_id, bearer, records)
            else:
                _write(self.path, records)
        return IssuedToken(token_id, bearer)

    def _save_locally(
        self, token_id: str, bearer: str, records: dict[str, _InstanceRecord]
    ) -> None:
        local = local_token_path()
        with _locked(local):
            local_records = _read_local(local)
            entries = local_records.get(str(self._data_dir), {})
            local_records[str(self._data_dir)] = {**entries, token_id: bearer}
            _write(self.path, records)
            try:
                _write(local, local_records)
            except OSError:
                # The local credential was never published. Remove its orphan hash if possible.
                del records[token_id]
                with contextlib.suppress(OSError):
                    _write(self.path, records)
                raise

    def revoke(self, token_id: str, *, local: bool = True) -> None:
        if not _ID.fullmatch(token_id):
            raise TokenError("invalid token ID")
        with _locked(self.path):
            records = _read_instance(self.path)
            if token_id in records:
                del records[token_id]
                _write(self.path, records)
            if local:
                try:
                    self._remove_local(token_id)
                except (TokenError, OSError) as exc:
                    raise LocalCleanupError(exc) from exc

    def _remove_local(self, token_id: str) -> None:
        path = local_token_path()
        with _locked(path):
            local_records = _read_local(path)
            entries = local_records.get(str(self._data_dir), {})
            if token_id in entries:
                del entries[token_id]
                _write(path, local_records)

    async def has_role(self, project_id: str, role: str) -> bool:
        """Check whether the instance has a token for the project role."""
        try:
            records = await asyncio.to_thread(_read_instance, self.path)
        except OSError, TokenError:
            return False
        return any(
            record["project"] == project_id and record["role"] == role
            for record in records.values()
        )

    async def authenticate(self, bearer: str) -> Identity | None:
        """Read the current atomic snapshot so the next request sees issue and revoke."""
        match = _BEARER.fullmatch(bearer)
        if match is None:
            return None
        try:
            return await asyncio.to_thread(self._authenticate_from_disk, match[1], bearer)
        except OSError, TokenError:
            return None

    def _authenticate_from_disk(self, token_id: str, bearer: str) -> Identity | None:
        records = _read(self.path)
        raw: Any = records.get(token_id)
        if not isinstance(raw, dict):
            return None
        digest = cast("dict[str, Any]", raw).get("hash")
        if (
            not isinstance(digest, str)
            or not _HASH.fullmatch(digest)
            or not hmac.compare_digest(_digest(bearer), digest)
        ):
            return None
        record = _validate_instance(self.path, records)[token_id]
        return Identity(record["project"], record["role"])
