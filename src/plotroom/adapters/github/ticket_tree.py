"""Read BMad ticket data from a pinned default-branch commit."""

import asyncio
import base64
import re
import tempfile
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Self, cast
from urllib.parse import quote

import httpx

from plotroom.adapters.github import GitHubCredential, GitHubRateLimited, GitHubUnavailable
from plotroom.adapters.github.read import GitHubReadClient, Row, obj
from plotroom.ports.work_items import MAX_WORK_ITEM_BODY_BYTES, WorkItem

_STORE_CONFIG = PurePosixPath("_bmad/custom/ticketing-store-config.toml")
_MAX_ENTRIES = 512
_MAX_FILE_BYTES = MAX_WORK_ITEM_BODY_BYTES
_MAX_TREE_BYTES = 16 * 1024 * 1024
_MAX_DIRECTORY_ENTRIES = 4096
_BLOB_BATCH = 8
_BODY_CACHE_ENTRIES = 16
_FETCH_TIMEOUT = 60
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


class GitHubTicketTreeError(ValueError):
    """The pinned ticket tree cannot be read safely or completely."""


def validate_tree_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or ".." in path.parts
        or ":" in value
        or "\\" in value
        or any(not char.isprintable() for char in value)
        or len(value.encode("utf-8")) > 1024
        or len(path.parts) > 32
    ):
        raise GitHubTicketTreeError("the GitHub ticket tree needs a repository-relative path")
    return path


def _destination(checkout: Path, relative: PurePosixPath) -> Path:
    destination = (checkout / relative).resolve()
    if not destination.is_relative_to(checkout):
        raise GitHubTicketTreeError("the ticket tree path leaves its temporary checkout")
    return destination


def _object(value: object) -> Row:
    try:
        return obj(value)
    except ValueError:
        raise GitHubTicketTreeError("GitHub returned invalid ticket tree metadata") from None


def _sha(value: object) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise GitHubTicketTreeError("GitHub returned an invalid Git object ID")
    return value


def _ticket_directory(path: PurePosixPath) -> bool:
    """Folder layouts the local BMad reader scans inside a ticket scope."""
    parts = path.parts
    return parts in ((), ("tickets",)) or (
        len(parts) >= 2
        and parts[0] == "tickets"
        and parts[1].startswith("epic-")
        and parts[2:] in ((), ("tickets",))
    )


@dataclass(frozen=True)
class _Blob:
    sha: str
    size: int

    @classmethod
    def from_entry(cls, entry: Row) -> Self:
        if entry.get("type") != "blob" or entry.get("mode") not in ("100644", "100755"):
            raise GitHubTicketTreeError("GitHub ticket data must be stored as regular files")
        size = entry.get("size")
        if type(size) is not int or not 0 <= size <= _MAX_FILE_BYTES:
            raise GitHubTicketTreeError("a GitHub ticket tree file exceeds the size limit")
        return cls(_sha(entry.get("sha")), size)


class _GitObjects:
    def __init__(self, api: GitHubReadClient, client: httpx.AsyncClient, token: str) -> None:
        self._api = api
        self._client = client
        self._token = token
        self._directories: dict[str, dict[str, Row]] = {}

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Row:
        try:
            value = await self._api.get(self._client, self._token, path, params)
        except ValueError:
            raise GitHubTicketTreeError("GitHub returned invalid ticket tree metadata") from None
        return _object(value)

    async def default_commit(self) -> tuple[str, str]:
        branch = (await self._get("")).get("default_branch")
        if not isinstance(branch, str) or not branch or branch == "." or branch == "..":
            raise GitHubTicketTreeError("GitHub did not report a default branch")
        details = await self._get(f"/branches/{quote(branch, safe='')}")
        commit = _object(details.get("commit"))
        tree = _object(_object(commit.get("commit")).get("tree"))
        return _sha(commit.get("sha")), _sha(tree.get("sha"))

    async def _entries(self, sha: str, *, recursive: bool) -> dict[str, Row]:
        tree = await self._get(f"/git/trees/{sha}", {"recursive": "1"} if recursive else None)
        raw = tree.get("tree")
        if tree.get("truncated") is not False or not isinstance(raw, list):
            raise GitHubTicketTreeError("GitHub returned an incomplete ticket tree")
        values = cast("list[object]", raw)
        if not recursive and len(values) > _MAX_DIRECTORY_ENTRIES:
            raise GitHubTicketTreeError("a GitHub ticket tree directory exceeds the entry limit")
        entries: dict[str, Row] = {}
        for value in values:
            entry = _object(value)
            path = entry.get("path")
            if not isinstance(path, str) or path == "." or path in entries:
                raise GitHubTicketTreeError("GitHub returned an invalid ticket tree path")
            parts = validate_tree_path(path).parts
            if not recursive and len(parts) != 1:
                raise GitHubTicketTreeError("GitHub returned an invalid directory entry")
            entries[path] = entry
        return entries

    async def locate(self, root: str, path: PurePosixPath) -> Row | None:
        entry: Row = {"type": "tree", "sha": root}
        for part in path.parts:
            if entry.get("type") != "tree":
                raise GitHubTicketTreeError("a GitHub ticket directory is not a Git tree")
            sha = _sha(entry.get("sha"))
            if sha not in self._directories:
                self._directories[sha] = await self._entries(sha, recursive=False)
            candidate = self._directories[sha].get(part)
            if candidate is None:
                return None
            entry = candidate
        return entry

    async def files(
        self, sha: str, scope: PurePosixPath
    ) -> tuple[tuple[PurePosixPath, ...], dict[PurePosixPath, _Blob]]:
        directories: list[PurePosixPath] = []
        files: dict[PurePosixPath, _Blob] = {}
        for path, entry in (await self._entries(sha, recursive=True)).items():
            relative = PurePosixPath(path)
            if entry.get("type") == "tree":
                # Keep old-layout epic folders so the reader can still reject that layout.
                if _ticket_directory(relative) or (
                    len(relative.parts) == 1 and relative.name.startswith("epic-")
                ):
                    directories.append(scope / relative)
                continue
            if _ticket_directory(relative.parent) and (
                relative.suffix == ".md" or relative.name == "tickets.toml"
            ):
                files[scope / relative] = _Blob.from_entry(entry)
        return tuple(directories), files

    async def config(self, sha: str, tree: PurePosixPath) -> tuple[PurePosixPath, _Blob] | None:
        # Match the local reader's nearest BMad project root, including in a monorepo.
        for directory in (tree, *tree.parents):
            bmad = await self.locate(sha, directory / "_bmad")
            if bmad is not None and bmad.get("type") == "tree":
                path = directory / _STORE_CONFIG
                entry = await self.locate(sha, path)
                return (path, _Blob.from_entry(entry)) if entry is not None else None
        return None

    async def blob(self, blob: _Blob) -> bytes:
        item = await self._get(f"/git/blobs/{blob.sha}")
        content = item.get("content")
        if (
            item.get("encoding") != "base64"
            or not isinstance(content, str)
            or item.get("size") != blob.size
            or len(content) > blob.size * 2 + 16
        ):
            raise GitHubTicketTreeError("GitHub returned an invalid ticket file")
        try:
            decoded = base64.b64decode(content.replace("\n", ""), validate=True)
        except ValueError:
            raise GitHubTicketTreeError("GitHub returned invalid ticket file encoding") from None
        if len(decoded) != blob.size:
            raise GitHubTicketTreeError("GitHub returned an incomplete ticket file")
        return decoded


class GitHubTicketTree:
    def __init__(
        self,
        api: GitHubReadClient,
        credential: GitHubCredential,
        tree: str,
        read_tree: Callable[[Path, Path], Awaitable[tuple[WorkItem, ...]]],
        *,
        read_body: Callable[[Path, Path, str], Awaitable[str | None]],
        transport: httpx.AsyncBaseTransport | None = None,
        cooling_down: Callable[[], Awaitable[bool]] | None = None,
        report_rate_limit: Callable[[GitHubRateLimited], Awaitable[None]] | None = None,
    ) -> None:
        self._api = api
        self._credential = credential
        self._tree = validate_tree_path(tree)
        self._read_tree = read_tree
        self._read_body = read_body
        self._transport = transport
        self._cooling_down = cooling_down
        self._report_rate_limit = report_rate_limit
        self._cached: tuple[str, tuple[WorkItem, ...]] | None = None
        self._body_cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._retry_until: datetime | None = None

    async def ready_items(self) -> tuple[WorkItem, ...]:
        return await self._fetch(self._read_commit)

    async def body(self, ref: str) -> str | None:
        async def read(git: _GitObjects) -> str | None:
            commit, root_sha = await git.default_commit()
            key = (commit, ref)
            if key in self._body_cache:
                self._body_cache.move_to_end(key)
                return self._body_cache[key]
            body = await self._read_tree_at(
                git, root_sha, lambda tree, root: self._read_body(tree, root, ref)
            )
            if body is not None:
                self._body_cache[key] = body
                self._body_cache.move_to_end(key)
                if len(self._body_cache) > _BODY_CACHE_ENTRIES:
                    self._body_cache.popitem(last=False)
            return body

        return await self._fetch(read)

    async def _fetch[T](self, read: Callable[[_GitObjects], Awaitable[T]]) -> T:
        if self._cooling_down is not None and await self._cooling_down():
            raise GitHubTicketTreeError("GitHub rate limit reached")
        if self._retry_until is not None and datetime.now(UTC) < self._retry_until:
            raise GitHubTicketTreeError("GitHub rate limit reached")
        try:
            token = await self._credential.token()
            async with (
                httpx.AsyncClient(
                    timeout=15, follow_redirects=False, trust_env=False, transport=self._transport
                ) as client,
                asyncio.timeout(_FETCH_TIMEOUT),
            ):
                return await read(_GitObjects(self._api, client, token))
        except GitHubRateLimited as exc:
            if self._report_rate_limit is not None:
                await self._report_rate_limit(exc)
            else:
                self._retry_until = exc.retry_at(datetime.now(UTC), 60)
            raise GitHubTicketTreeError("GitHub rate limit reached") from None
        except GitHubUnavailable, httpx.RequestError, TimeoutError:
            raise GitHubTicketTreeError("GitHub ticket tree request failed") from None

    async def _read_commit(self, git: _GitObjects) -> tuple[WorkItem, ...]:
        commit, root_sha = await git.default_commit()
        if self._cached is not None and self._cached[0] == commit:
            return self._cached[1]
        items = await self._read_tree_at(git, root_sha, self._read_tree)
        self._cached = (commit, items)
        return items

    async def _read_tree_at[T](
        self, git: _GitObjects, root_sha: str, read: Callable[[Path, Path], Awaitable[T]]
    ) -> T:
        scope = self._tree.parent if self._tree.name == "tickets" else self._tree
        if scope.name.startswith("epic-") and scope.parent.name == "tickets":
            scope = scope.parent.parent
        selected = await git.locate(root_sha, self._tree)
        if selected is None or selected.get("type") != "tree":
            raise GitHubTicketTreeError("the configured GitHub ticket tree does not exist")
        root = selected if scope == self._tree else await git.locate(root_sha, scope)
        if root is None or root.get("type") != "tree":
            raise GitHubTicketTreeError(
                "the configured GitHub ticket tree has no enclosing Git tree"
            )
        directories, files = await git.files(_sha(root.get("sha")), scope)
        config = await git.config(root_sha, self._tree)
        project = self._tree
        if config is not None:
            path, blob = config
            files[path] = blob
            project = path.parents[2]
        if (
            len(directories) + len(files) > _MAX_ENTRIES
            or sum(blob.size for blob in files.values()) > _MAX_TREE_BYTES
        ):
            raise GitHubTicketTreeError("the GitHub ticket tree exceeds the snapshot limit")
        with tempfile.TemporaryDirectory(prefix="plotroom-tickets-") as directory:
            checkout = await asyncio.to_thread(Path(directory).resolve)
            tree = _destination(checkout, self._tree)
            tree.mkdir(parents=True, exist_ok=True)
            for child in directories:
                _destination(checkout, child).mkdir(parents=True, exist_ok=True)
            await self._materialize(git, checkout, files)
            return await read(tree, _destination(checkout, project))

    @staticmethod
    async def _materialize(
        git: _GitObjects, checkout: Path, files: dict[PurePosixPath, _Blob]
    ) -> None:
        entries = list(files.items())
        for offset in range(0, len(entries), _BLOB_BATCH):
            batch = entries[offset : offset + _BLOB_BATCH]
            bodies = await asyncio.gather(
                *(git.blob(blob) for _, blob in batch), return_exceptions=True
            )
            for (relative, _), content in zip(batch, bodies, strict=True):
                if isinstance(content, BaseException):
                    raise content
                path = _destination(checkout, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as file:
                    file.write(content)
