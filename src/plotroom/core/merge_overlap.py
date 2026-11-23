"""Pinned net file overlap and head-specific label bindings."""

import re
from dataclasses import replace
from typing import cast

from plotroom.core.github import PullRequest
from plotroom.core.github_projection import GitHubResource

PULL_COMPARISON = "pull_comparison"
ALLOW_OVERLAP = "allow-overlap"


def full_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{40}", value) is not None


def pinned_overlap(
    resource: GitHubResource | None,
    pull: PullRequest,
    branch: str,
    main_tip: str,
) -> tuple[str, tuple[str, ...]] | None:
    if resource is None or not resource.complete:
        return None
    attributes = resource.attributes
    base = attributes.get("merge_base")
    if (
        attributes.get("head_sha") != pull.head_sha
        or attributes.get("pull_version") != pull.version
        or attributes.get("pull_updated_at") != pull.updated_at
        or attributes.get("branch") != branch
        or attributes.get("main_tip") != main_tip
        or not full_sha(pull.head_sha)
        or not full_sha(main_tip)
        or not isinstance(base, str)
        or not full_sha(base)
    ):
        return None
    raw_pr, raw_main = attributes.get("pull_paths"), attributes.get("main_paths")
    if not isinstance(raw_pr, list) or not isinstance(raw_main, list):
        return None
    pr_files, main_files = cast("list[object]", raw_pr), cast("list[object]", raw_main)
    if any(not isinstance(path, str) or not path for path in (*pr_files, *main_files)):
        return None
    paths = set(cast("list[str]", pr_files)).intersection(cast("list[str]", main_files))
    return base, tuple(sorted(paths))


def bind_overlap_labels(
    result: GitHubResource, previous: GitHubResource | None, pull: PullRequest
) -> GitHubResource:
    """Grant only a newly observed label event across two polls of the same head."""
    bindings = dict(previous.attributes.get("overlap_bindings", {})) if previous else {}
    known: set[str] = {record.key for record in previous.records} if previous else set()
    same_head = (
        previous is not None
        and previous.complete
        and previous.attributes.get("head_sha") == pull.head_sha
        and previous.attributes.get("pull_version") == pull.version
    )
    for record in result.records:
        if record.fields.get("name") == ALLOW_OVERLAP and record.fields.get("action") == "labeled":
            binding = (
                [pull.head_sha, pull.version] if same_head and record.key not in known else None
            )
            bindings.setdefault(record.key, binding)
    return replace(result, attributes={**result.attributes, "overlap_bindings": bindings})


def overlap_label_is_bound(labels: GitHubResource | None, pull: PullRequest, event: int) -> bool:
    bindings = labels.attributes.get("overlap_bindings") if labels else None
    return isinstance(bindings, dict) and cast("dict[str, object]", bindings).get(str(event)) == [
        pull.head_sha,
        pull.version,
    ]
