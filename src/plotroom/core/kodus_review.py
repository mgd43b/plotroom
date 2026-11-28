"""Configured trust for Kodus review evidence."""

from dataclasses import dataclass

from plotroom.core.github_projection import GitHubResource, checks_for_head

KODUS_CHECK_FINGERPRINT = "kodus_check_fingerprint"


@dataclass(frozen=True)
class OpeningHeadAttestation:
    repository: str
    pull_number: int
    pull_id: int
    created_at: str
    head_sha: str


@dataclass(frozen=True)
class KodusReviewPolicy:
    app_id: int
    app_slug: str
    check_name: str
    opening_heads: tuple[OpeningHeadAttestation, ...] = ()

    def opening_head(self, repository: str, pull_number: int) -> OpeningHeadAttestation | None:
        matches = [
            entry
            for entry in self.opening_heads
            if (entry.repository, entry.pull_number) == (repository, pull_number)
        ]
        return matches[0] if len(matches) == 1 else None


def trusted_current_check(
    policy: KodusReviewPolicy,
    checks: GitHubResource,
    head_sha: str,
    check_id: int,
    fingerprint: str,
    conclusion: str,
) -> bool:
    """The independent check read and ordinary checks must agree on the current attempt."""
    latest = checks_for_head(checks, head_sha)
    return latest is not None and any(
        check.fields.get("id") == check_id
        and check.fields.get("name") == policy.check_name
        and check.fields.get("status") == "completed"
        and check.fields.get("conclusion") == conclusion
        and check.details.get("app_id") == policy.app_id
        and check.details.get(KODUS_CHECK_FINGERPRINT) == fingerprint
        for check in latest
    )
