"""Offline scope and evidence checks for the manual GitHub fixture observer."""

import importlib
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from plotroom.core.events import Event

BASE = "a" * 40
HEAD = "b" * 40
BRANCH = "plotroom-33/fixture"


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.setattr(sys, "path", [str(scripts), *sys.path])
    return importlib.import_module("github_fixture_observer")


def test_inputs_require_a_literal_fixture_branch_and_distinct_full_heads(module: Any) -> None:
    state = module.args_state(BASE, HEAD, BRANCH)
    assert state["issue_title"] == "Plotroom fixture 33 fixture"
    for branch in ("main", "plotroom-33/*", "plotroom-33/../../main", "plotroom-33/UPPER"):
        with pytest.raises(module.AcceptanceError, match="invalid fixture branch"):
            module.args_state(BASE, HEAD, branch)
    with pytest.raises(module.AcceptanceError, match="distinct prepared commit"):
        module.args_state(BASE, BASE, BRANCH)
    with pytest.raises(module.AcceptanceError, match="invalid commit SHA"):
        module.args_state(BASE[:8], HEAD, BRANCH)


@pytest.mark.parametrize("drift", ["", "base", "head"])
async def test_observer_pins_fixture_and_refuses_writes_even_when_armed(
    module: Any, drift: str
) -> None:
    calls: list[str] = []

    def reply(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        path = request.url.path.removeprefix("/repos/mgd43b/plotroom-test")
        calls.append(path)
        payload = {
            "": {
                "full_name": "mgd43b/plotroom-test",
                "default_branch": "main",
                "private": False,
                "fork": False,
            },
            "/branches/main": {"commit": {"sha": HEAD if drift == "base" else BASE}},
            f"/git/ref/heads/{BRANCH}": {
                "ref": f"refs/heads/{BRANCH}",
                "object": {"sha": BASE if drift == "head" else HEAD},
            },
        }
        return httpx.Response(200, json=payload[path])

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        provider = module.ReadOnlyProvider(client, acceptance=True)
        provider.preflight_passed = True
        with pytest.raises(module.AcceptanceError, match="only permits GET"):
            await provider.request("POST", "/pulls")
        assert calls == []
        if drift:
            with pytest.raises(module.AcceptanceError, match=r"moved|tip changed"):
                await module.pin_provider(provider, module.args_state(BASE, HEAD, BRANCH))
        else:
            await module.pin_provider(provider, module.args_state(BASE, HEAD, BRANCH))
            assert calls == ["", "/branches/main", f"/git/ref/heads/{BRANCH}"]


def check_event(seq: int, state: str, conclusion: str | None = None) -> Event:
    return Event(
        seq,
        f"event-{seq}",
        "github",
        "github.check_run",
        f"check-{seq}",
        None,
        {"id": 7, "status": state, "conclusion": conclusion, "head_sha": HEAD},
        "",
        "2026-11-26T00:00:00Z",
    )


def test_check_state_recurrence_is_valid_but_adjacent_duplicate_is_refused(module: Any) -> None:
    first = check_event(1, "queued")
    work = check_event(2, "in_progress")
    requeued = check_event(3, "queued")
    done = check_event(4, "completed", "success")
    module.validate_check_events([first, work, requeued, done])
    with pytest.raises(module.AcceptanceError, match="duplicated check facts"):
        module.validate_check_events([first, work, check_event(5, "in_progress")])
