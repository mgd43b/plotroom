"""Two expected-head operations for the local GitHub write fixture."""

from dataclasses import dataclass
from typing import Literal, Protocol

from plotroom.core.merge_gate import GateSnapshot

type BranchUpdateOutcome = Literal["updated", "conflict"]


@dataclass(frozen=True)
class BranchUpdateReceipt:
    outcome: BranchUpdateOutcome
    main_sha: str


class FixtureGitHubWrite(Protocol):
    async def update_branch(
        self,
        *,
        repository: str,
        number: int,
        action_key: str,
        expected_head_sha: str,
        expected_main_sha: str | None,
        recovery_only: bool = False,
    ) -> BranchUpdateReceipt | None:
        """Apply once per action key and return the recorded outcome on retry.

        Return the recorded receipt before testing current SHAs. Recovery-only
        calls can only return a receipt. New effects require both exact SHAs.
        A missing receipt in recovery mode or a ref mismatch returns None.
        """
        ...

    async def merge(
        self,
        *,
        repository: str,
        number: int,
        action_key: str,
        expected_head_sha: str,
        expected_main_sha: str,
        method: Literal["merge", "squash", "rebase"],
        evidence_revision: str,
    ) -> bool:
        """Atomically apply the exact evidence revision, refs, and method at most once."""
        ...


@dataclass(frozen=True)
class FixtureMergeEvidence:
    snapshot: GateSnapshot | None
    revision: str
    applied: bool = False


class FixtureGitHubMerge(FixtureGitHubWrite, Protocol):
    async def merge_evidence(
        self, *, repository: str, number: int, action_key: str
    ) -> FixtureMergeEvidence:
        """Read authoritative current facts or a durable receipt for this exact action key."""
        ...
