"""Two expected-head operations for the local GitHub write fixture."""

from dataclasses import dataclass
from typing import Literal, Protocol

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
        self, *, repository: str, number: int, action_key: str, expected_head_sha: str
    ) -> None: ...
