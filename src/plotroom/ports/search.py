"""A project's searchable records and their transaction-bound write path."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from plotroom.core.events import Event
from plotroom.ports.store import Transaction

DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100
MAX_SEARCH_QUERY_LENGTH = 512
MESSAGE_KIND = "switchboard.message"
DECISION_KIND = "decision"
ASK_KIND = "ask"
ISSUE_COMMENT_KIND = "github.issue_comment"
PR_BODY_KIND = "github.pr_body"


@dataclass(frozen=True)
class SearchRecord:
    kind: str
    id: str
    body: str


@dataclass(frozen=True)
class SearchHit:
    id: str
    kind: str
    excerpt: str
    # Half-open bounds of the lexical match within the excerpt.
    match_span: tuple[int, int] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class SearchPage:
    hits: tuple[SearchHit, ...]
    total: int
    mode: str | None = None
    fallback_reason: str | None = None


class Search(Protocol):
    @property
    def project_id(self) -> str: ...

    async def index(self, tx: Transaction, event: Event) -> None:
        """Project a new event using the same writer transaction that stored it."""
        ...

    async def index_record(
        self, tx: Transaction, record: SearchRecord, *, only_if_absent: bool = False
    ) -> None:
        """Store the current source text, preserving a present record during backfill."""
        ...

    async def query(
        self, project: str, text: str, kinds: tuple[str, ...] | None, limit: int
    ) -> SearchPage:
        """Find a literal phrase and count all matches in the selected project and kinds."""
        ...


class BackfillSource(StrEnum):
    EVENTS = "events"
    DECISIONS = "decisions"
    ASKS = "asks"
    COMMENTS = "comments"
    PULLS = "pulls"


# An empty position starts a source. None marks it complete.
type BackfillPosition = tuple[str | int, ...] | None


@dataclass(frozen=True)
class BackfillPage:
    through: BackfillPosition
    records: tuple[SearchRecord, ...] = ()


class SearchBackfill(Protocol):
    @property
    def project_id(self) -> str: ...

    async def backfill_position(self, source: BackfillSource) -> BackfillPosition: ...

    async def backfill_page(
        self, source: BackfillSource, after: tuple[str | int, ...], limit: int
    ) -> BackfillPage: ...

    async def commit_backfill(
        self,
        tx: Transaction,
        source: BackfillSource,
        expected: BackfillPosition,
        page: BackfillPage,
    ) -> bool:
        """Insert missing records and advance the checkpoint from the expected position."""
        ...


class EmbeddingError(Exception):
    """The provider failed to return a usable batch."""


class EmbeddingPending(Exception):
    """At least one requested source is still being indexed."""


@dataclass(frozen=True)
class EmbeddingModel:
    name: str
    dimension: int


@dataclass(frozen=True)
class EmbeddedRecord:
    seq: int
    kind: str
    id: str
    vector: bytes


@dataclass(frozen=True)
class EmbeddingRevision:
    seq: int
    record: SearchRecord | None


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

    async def revisions_after(self, after: int, limit: int) -> list[EmbeddingRevision]: ...

    async def ready_count(self, kinds: tuple[str, ...]) -> int | None:
        """Return the eligible count, or None while requested sources are incomplete."""
        ...

    async def commit(
        self, tx: Transaction, expected: int, through: int, records: tuple[EmbeddedRecord, ...]
    ) -> bool:
        """Write the batch and advance its cursor together, or return False for a stale attempt."""
        ...

    async def query_vector(
        self,
        project: str,
        vector: tuple[float, ...],
        limit: int,
        *,
        kinds: tuple[str, ...] | None = None,
    ) -> SearchPage: ...
