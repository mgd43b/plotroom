"""A project's searchable message index and its transaction-bound write path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from plotroom.core.events import Event
from plotroom.ports.store import Transaction

DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100
MAX_SEARCH_QUERY_LENGTH = 512
MESSAGE_KIND = "switchboard.message"


@dataclass(frozen=True)
class SearchHit:
    id: str
    kind: str
    excerpt: str


@dataclass(frozen=True)
class SearchPage:
    hits: tuple[SearchHit, ...]
    total: int


class Search(Protocol):
    @property
    def project_id(self) -> str: ...

    async def index(self, tx: Transaction, event: Event) -> None:
        """Project a new event using the same writer transaction that stored it."""
        ...

    async def query(
        self, project: str, text: str, kinds: tuple[str, ...] | None, limit: int
    ) -> SearchPage:
        """Find a literal phrase and count all matches in the selected project and kinds."""
        ...


class EmbeddingError(Exception):
    """The provider failed to return a usable batch."""


@dataclass(frozen=True)
class EmbeddingModel:
    name: str
    dimension: int


@dataclass(frozen=True)
class EmbeddedMessage:
    id: str
    vector: bytes


class Embedder(Protocol):
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Return one vector per input, in input order."""
        ...


class EmbeddingIndex(Protocol):
    @property
    def project_id(self) -> str: ...

    @property
    def model(self) -> EmbeddingModel: ...

    async def cursor(self) -> int: ...

    async def commit(
        self, tx: Transaction, expected: int, through: int, records: tuple[EmbeddedMessage, ...]
    ) -> bool:
        """Write the batch and advance its cursor together, or return False for a stale attempt."""
        ...

    async def query_vector(
        self, project: str, vector: tuple[float, ...], limit: int
    ) -> SearchPage: ...
