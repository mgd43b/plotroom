"""Clerk Rule 5 over complete, current PR text and human label evidence."""

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from fakes import commit_status_evidence
from plotroom.adapters.github.pr_details import fetch_pull_text
from plotroom.adapters.github.read import GitHubReadClient
from plotroom.core.events import format_time
from plotroom.core.github_projection import GitHubResource
from plotroom.core.merge_gate import KEYWORDS_OK, GateSnapshot, evaluate_gate
from test_github_details import API, FIRST, MAIN_SHA, NOW, REPO, SECOND
from test_merge_gate_main import BRANCH, POLICY, main, record
from test_merge_gate_rule2 import CHECK, CHECKED_AT, EMPTY_THREADS, PULL

PINS = {"head_sha": FIRST, "pull_version": PULL.version, "pull_updated_at": NOW}
TEXT_OBSERVED = format_time(CHECKED_AT - timedelta(seconds=10))
SNAPSHOT = GateSnapshot(
    PULL,
    GitHubResource((CHECK,), as_of=NOW),
    commit_statuses=commit_status_evidence(REPO, FIRST, NOW),
    threads=EMPTY_THREADS,
    main_ci=main(record(3, "success")),
    main=GitHubResource((), as_of=NOW, attributes={"branch": BRANCH, "head_sha": MAIN_SHA}),
    comparison=GitHubResource(
        (),
        as_of=NOW,
        attributes={
            **PINS,
            "branch": BRANCH,
            "main_tip": MAIN_SHA,
            "merge_base": SECOND,
            "pull_paths": ["pull.txt"],
            "main_paths": ["main.txt"],
        },
    ),
)


def text(body: str, message: str) -> GitHubResource:
    return GitHubResource(
        (),
        as_of=TEXT_OBSERVED,
        attributes={**PINS, "body": body, "commit_count": 1, "commit_messages": [message]},
    )


@pytest.mark.parametrize(
    ("body", "message", "exit_class"),
    [
        ("", "Header\n\ncloses #1", 4),
        ("RESOLVED #123", "Refs #1", 4),
        ("(fix #02)", "Refs #1", 4),
        ("Refs #1", "Refs #2", 0),
        ("éfix #2", "", 4),
        ("prefixfix #1\nf\u0131x #3", "fix\t#4\nfix  #5\nfix #\uff16\nfix repo#7", 0),
        ("close\n#1", "fix", 0),
    ],
)
def test_rule5_clerk_pattern_and_watermark(body: str, message: str, exit_class: int) -> None:
    verdict = evaluate_gate(replace(SNAPSHOT, pull_text=text(body, message)), POLICY, CHECKED_AT)
    assert (verdict.exit_class, verdict.as_of) == (exit_class, TEXT_OBSERVED)
    if exit_class:
        assert [reason.code for reason in verdict.reasons] == ["closing_keywords"]


@pytest.mark.parametrize(
    ("login", "kind", "active", "same_head", "exit_class"),
    [
        ("Editor", "User", True, True, 0),
        ("other", "User", True, True, 4),
        ("editor", "Bot", True, True, 4),
        ("editor", None, True, True, 4),
        ("editor", "User", False, True, 4),
        ("editor", "User", True, False, 4),
    ],
)
def test_only_current_authorized_human_label_waives_closing_keywords(
    login: str, kind: str | None, active: bool, same_head: bool, exit_class: int
) -> None:
    labels = GitHubResource(
        (),
        as_of=NOW,
        attributes={
            **PINS,
            "head_sha": FIRST if same_head else SECOND,
            "labels": {KEYWORDS_OK: login} if active else {},
            "label_provenance": {KEYWORDS_OK: {"login": login, "actor_type": kind, "event_id": 5}},
        },
    )
    snapshot = replace(SNAPSHOT, labels=labels, pull_text=text("Fixes #7", "Refs #1"))
    assert evaluate_gate(snapshot, POLICY, CHECKED_AT).exit_class == exit_class


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "incomplete",
        "stale",
        "head_sha",
        "pull_version",
        "pull_updated_at",
        "body",
        "commit_count",
        "commit_messages",
    ],
)
def test_missing_stale_unpinned_or_malformed_text_fails_closed(fault: str) -> None:
    observation = text("", "Refs #1")
    if fault == "incomplete":
        observation = replace(observation, complete=False)
    elif fault == "stale":
        observation = replace(observation, as_of=format_time(CHECKED_AT - timedelta(minutes=3)))
    else:
        observation = replace(observation, attributes={**observation.attributes, fault: None})
    snapshot = replace(SNAPSHOT, pull_text=None if fault == "missing" else observation)
    verdict = evaluate_gate(snapshot, POLICY, CHECKED_AT)
    assert verdict.exit_class == 2
    assert [reason.code for reason in verdict.reasons] == ["pull_text_unreadable"]


@pytest.mark.parametrize(
    "fault",
    [None, "short", "wrong_final", "duplicate", "bad_message", "head", "time", "count", "body"],
)
async def test_fetch_pr_text_confirms_metadata_and_entire_commit_list_across_pages(
    fault: str | None,
) -> None:
    calls: list[str] = []
    meta = {"head": {"sha": FIRST}, "updated_at": NOW, "commits": 101, "body": None}

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        path = request.url.path
        assert path.startswith(f"/repos/{REPO}/pulls/{PULL.number}")
        if path.endswith("/commits"):
            page = request.url.params["page"]
            calls.append(page)
            if page == "1":
                return httpx.Response(
                    200,
                    json=[
                        {"sha": f"{index:040x}", "commit": {"message": "Refs #1"}}
                        for index in range(100)
                    ],
                )
            assert page == "2"
            sha = {"wrong_final": SECOND, "duplicate": f"{0:040x}"}.get(fault or "", FIRST)
            message = None if fault == "bad_message" else "Subject\n\nCloses #2"
            return httpx.Response(
                200, json=[] if fault == "short" else [{"sha": sha, "commit": {"message": message}}]
            )
        calls.append("pull")
        current = dict(meta)
        if len(calls) > 1:
            if fault == "head":
                current["head"] = {"sha": SECOND}
            elif fault == "time":
                current["updated_at"] = format_time(CHECKED_AT + timedelta(seconds=1))
            elif fault == "count":
                current["commits"] = 100
            elif fault == "body":
                current["body"] = "Edited body"
        return httpx.Response(200, json=current)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        api = GitHubReadClient(API, REPO)
        if fault is not None:
            with pytest.raises(ValueError, match=r"PR|expected a string"):
                await fetch_pull_text(api, client, "fixture", PULL)
            assert calls[:3] == ["pull", "1", "2"]
            return
        observed = await fetch_pull_text(api, client, "fixture", PULL)
    assert calls == ["pull", "1", "2", "pull"]
    assert observed.records == ()
    assert observed.attributes == {
        **PINS,
        "body": "",
        "commit_count": 101,
        "commit_messages": ["Refs #1"] * 100 + ["Subject\n\nCloses #2"],
    }
