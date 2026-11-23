"""Two expected-head operations for the local GitHub write fixture."""

from typing import Literal, Protocol

type BranchUpdateOutcome = Literal["updated", "conflict", "moved"]


class FixtureGitHubWrite(Protocol):
    async def update_branch(
        self,
        *,
        repository: str,
        number: int,
        action_key: str,
        expected_head_sha: str,
        expected_main_sha: str,
    ) -> BranchUpdateOutcome:
        """Apply once per action key and return the recorded outcome on retry.

        Check both expected SHAs before applying a new key. An existing outcome is
        returned before testing current SHAs, since the update itself can move the head.
        """
        ...

    async def merge(
        self, *, repository: str, number: int, action_key: str, expected_head_sha: str
    ) -> None: ...
