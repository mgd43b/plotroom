"""Synthetic scope and restart checks for the dedicated branch-update runner."""

import base64
import importlib
import json
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github/workflows/github-fixture-branch-update.yml"
)
SOURCE = "1" * 40
HEAD = "2" * 40
UPDATED = "3" * 40
FOREIGN = "4" * 40
BLOB = "5" * 40
PR = 62
OLD = b"# Plotroom test fixture\n\nSynthetic data for dedicated GitHub projection checks.\n"
MAIN_README = OLD + b"\nProjection trial.\n"
PREFIX = "/repos/mgd43b/plotroom-test"


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.setattr(sys, "path", [str(scripts), *sys.path])
    return importlib.import_module("github_fixture_branch_update")


def test_workflow_has_public_default_and_explicit_owner_write_step() -> None:
    workflow = cast("dict[str, Any]", yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader))
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["phase"]["default"] == "preflight"
    assert inputs["authorize_writes"]["default"] == "false"
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["fixture"]
    for guard in (
        "github.ref == 'refs/heads/main'",
        "github.actor == 'mgd43b'",
        "github.triggering_actor == 'mgd43b'",
        "github.run_attempt == '1'",
    ):
        assert guard in job["if"]
    assert workflow["concurrency"]["group"] == "github-fixture-acceptance"
    writers = [s for s in job["steps"] if "secrets." in str(s)]
    [step] = writers
    assert step["if"] == "inputs.phase == 'acceptance' && inputs.authorize_writes"
    assert "for phase in submit observe" in step["run"]
    assert "git ls-remote" in job["steps"][1]["run"]
    step_names = [s.get("name") for s in job["steps"]]
    assert step_names.index("Install locked dependencies") < step_names.index("Read-only preflight")
    assert step_names.index("Install locked dependencies") < step_names.index(step["name"])
    assert "upload-artifact" not in WORKFLOW.read_text()


@pytest.mark.parametrize(
    "override",
    [
        {},
        {"GITHUB_REF": "refs/heads/test"},
        {"GITHUB_REPOSITORY": "fixture/foreign"},
        {"GITHUB_WORKFLOW_REF": "mgd43b/plotroom/.github/workflows/other.yml@refs/heads/main"},
        {"GITHUB_SERVER_URL": "https://other.invalid"},
        {"GITHUB_EVENT_NAME": "pull_request_target"},
        {"GITHUB_TRIGGERING_ACTOR": "other"},
        {"GITHUB_RUN_ATTEMPT": "2"},
        {"GITHUB_SHA": FOREIGN},
        {"FIXTURE_WRITES_AUTHORIZED": "false"},
    ],
)
def test_script_revalidates_scope(module: Any, override: dict[str, str]) -> None:
    args = SimpleNamespace(
        phase="submit", source=SOURCE, ancestor=module.ANCESTOR, base=module.BASE
    )
    env = {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": module.SOURCE_REPO,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": SOURCE,
        "GITHUB_WORKFLOW_REF": f"{module.SOURCE_REPO}/{module.WORKFLOW_PATH}@refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_ACTOR": "mgd43b",
        "GITHUB_TRIGGERING_ACTOR": "mgd43b",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": "123",
        "FIXTURE_WRITES_AUTHORIZED": "true",
    }
    if override:
        with pytest.raises(module.AcceptanceError):
            module.scope_for(args, env | override)
    else:
        assert module.scope_for(args, env).branch == "plotroom-67/123"


class Server:
    """Only the synthetic fixture can answer these requests."""

    def __init__(self, module: Any, problem: str = "") -> None:
        self.module = module
        self.scope = module.Scope(SOURCE, module.ANCESTOR, module.BASE, "123")
        self.problem = problem
        self.writes: list[tuple[str, str, Any]] = []
        self.tip = self.scope.base
        self.head: str | None = None
        self.open = False
        self.created = False
        self.journal: Path | None = None

    def pull(self) -> dict[str, Any]:
        return {
            "number": PR,
            "state": "open" if self.open else "closed",
            "merged": False,
            "draft": False,
            "head": {
                "sha": self.head,
                "ref": self.scope.branch,
                "repo": {"full_name": self.module.REPO},
            },
            "base": {"sha": self.tip, "ref": "main", "repo": {"full_name": self.module.REPO}},
        }

    def reply(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert request.url.path.startswith(PREFIX)
        path = request.url.path.removeprefix(PREFIX)
        method = request.method
        if method != "GET":
            body = json.loads(request.content) if request.content else None
            self.writes.append((method, path, body))
            return self.mutate(request, path, body)
        if path == "":
            result: Any = {
                "id": self.module.REPO_ID,
                "full_name": self.module.REPO,
                "default_branch": "main",
                "private": False,
                "fork": False,
            }
        elif path == "/branches/main":
            result = {"commit": {"sha": self.tip}}
        elif path == self.scope.ref_path:
            if self.head is None:
                return httpx.Response(404)
            result = {"ref": self.scope.ref, "object": {"sha": self.head}}
        elif path == "/pulls":
            result = (
                [self.pull()]
                if self.open or (self.created and request.url.params["state"] == "all")
                else []
            )
            if self.problem == "ambiguous_cleanup" and request.url.params["state"] == "all":
                result.append({"number": PR + 1})
        elif path == f"/pulls/{PR}":
            result = self.pull()
        elif path == "/contents/README.md":
            data = {
                self.scope.ancestor: OLD,
                self.scope.base: MAIN_README,
                HEAD: self.module.edited(OLD),
                UPDATED: self.module.edited(MAIN_README),
            }[request.url.params["ref"]]
            result = {
                "encoding": "base64",
                "type": "file",
                "sha": BLOB,
                "content": base64.b64encode(data).decode("ascii"),
            }
        elif path.startswith("/compare/"):
            start, _, _ = path.removeprefix("/compare/").partition("...")
            result = {
                "status": "ahead",
                "base_commit": {"sha": start},
                "merge_base_commit": {"sha": start},
                "files": [{"filename": "README.md"}],
            }
        elif path == f"/git/commits/{HEAD}":
            result = {"sha": HEAD, "parents": [{"sha": self.scope.ancestor}]}
        elif path == f"/git/commits/{FOREIGN}":
            result = {"sha": FOREIGN, "parents": [{"sha": HEAD}]}
        elif path == f"/git/commits/{UPDATED}":
            result = {"sha": UPDATED, "parents": [{"sha": s} for s in (HEAD, self.scope.base)]}
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=result)

    def mutate(self, request: httpx.Request, path: str, body: Any) -> httpx.Response:
        if path == "/git/refs":
            assert body == {"ref": self.scope.ref, "sha": self.scope.ancestor}
            self.head = self.scope.ancestor
            return httpx.Response(201)
        if path == "/contents/README.md":
            assert body["branch"] == self.scope.branch
            assert body["sha"] == BLOB
            assert base64.b64decode(body["content"]) == self.module.edited(OLD)
            self.head = HEAD
            return httpx.Response(200, json={"commit": {"sha": self.head}})
        if path == "/pulls":
            assert body["head"] == self.scope.branch
            assert body["base"] == "main"
            self.created = self.open = True
            return httpx.Response(201, json={"number": PR})
        if path == f"/pulls/{PR}/update-branch":
            assert body == {"expected_head_sha": HEAD}
            assert self.journal is not None
            assert self.module.read_record(self.journal / "attempt.json") == {
                "pr": PR,
                "head": HEAD,
            }
            if self.problem == "redirect":
                return httpx.Response(307, headers={"Location": "https://other.invalid/"})
            if self.problem == "422":
                return httpx.Response(422)
            if self.problem != "timeout":
                self.head = FOREIGN if self.problem == "head_after" else UPDATED
            if self.problem == "base_after":
                self.tip = FOREIGN
            if self.problem == "lost":
                raise httpx.ReadTimeout("synthetic lost reply", request=request)
            return httpx.Response(202)
        if path == f"/pulls/{PR}":
            assert body == {"state": "closed"}
            self.open = False
            return httpx.Response(200)
        assert path == f"/git/refs/heads/{self.scope.branch}"
        assert request.method == "DELETE"
        self.head = None
        return httpx.Response(204)


async def test_default_read_has_no_write_route(module: Any) -> None:
    server = Server(module)
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.reply)) as client:
        fixture = module.Fixture(client, server.scope, "preflight")
        assert await module.preflight(fixture) == (module.edited(OLD), BLOB)
        with pytest.raises(module.AcceptanceError, match="outside"):
            await fixture._write(
                "POST", "/git/refs", json={"ref": server.scope.ref, "sha": server.scope.ancestor}
            )
        writer = module.Fixture(client, server.scope, "submit")
        with pytest.raises(module.AcceptanceError, match="outside"):
            await writer._write("PUT", "/contents/README.md", json={"branch": "main"})
        with pytest.raises(module.AcceptanceError, match="outside"):
            await writer._write("PUT", f"/pulls/{PR}/merge", json={"sha": HEAD})
        with pytest.raises(module.AcceptanceError, match="outside"):
            await writer._write(
                "PUT", f"/pulls/{PR}/update-branch", json={"expected_head_sha": HEAD}
            )
    assert server.writes == []


@pytest.mark.parametrize(
    ("problem", "error"),
    [
        ("", ""),
        ("lost", "response uncertain"),
        ("422", "observation timed out"),
        ("timeout", "observation timed out"),
        ("redirect", "observation timed out"),
        ("base_after", "main moved"),
        ("head_after", "lacks both planned parents"),
        ("ambiguous_cleanup", "ownership is ambiguous"),
    ],
)
async def test_lifecycle_uses_one_update_and_cleanup_requires_observation(
    module: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, problem: str, error: str
) -> None:
    server = Server(module, problem)
    server.journal = tmp_path

    async def ready_ci(*args: Any) -> int:
        return 80

    async def stale(_root: Path, scope: Any, pr: int, head: str) -> None:
        assert (scope, pr, head) == (server.scope, PR, HEAD)

    async def check_once(read: Any, predicate: Any, message: str) -> Any:
        value = await read()
        module.require(predicate(value), message)
        return value

    @asynccontextmanager
    async def observer(
        root: Path, *, initialize: bool
    ) -> AsyncGenerator[tuple[None, SimpleNamespace]]:
        assert root == tmp_path
        assert initialize is False

        async def events_after(*args: Any) -> list[Any]:
            return [
                SimpleNamespace(
                    kind=module.PR_HEAD_CHANGED, fields={"number": PR, "head_sha": UPDATED}
                )
            ]

        yield None, SimpleNamespace(events_after=events_after)

    monkeypatch.setattr(module.Fixture, "ready_ci", ready_ci)
    monkeypatch.setattr(module, "expect_stale", stale)
    monkeypatch.setattr(module, "eventually", check_once)
    monkeypatch.setattr(module, "observer", observer)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(server.reply), follow_redirects=True
    ) as first:
        await module.submit(module.Fixture(first, server.scope, "submit"), tmp_path)
    assert module.read_record(tmp_path / "attempt.json") == {"pr": PR, "head": HEAD}
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.reply)) as second:
        fixture = module.Fixture(second, server.scope, "observe")
        if error:
            with pytest.raises(module.AcceptanceError, match=error):
                await module.observe(fixture, tmp_path)
        else:
            await module.observe(fixture, tmp_path)
            assert server.head is None
            assert not server.open
            with pytest.raises(FileExistsError):
                await module.submit(module.Fixture(second, server.scope, "submit"), tmp_path)
        assert [path for _, path, _ in server.writes if path.endswith("/update-branch")] == [
            f"/pulls/{PR}/update-branch"
        ]
    assert [method for method, _, _ in server.writes] == (
        ["POST", "PUT", "POST", "PUT"]
        if error
        else ["POST", "PUT", "POST", "PUT", "PATCH", "DELETE"]
    )


@pytest.mark.parametrize("drift", ["head", "base"])
async def test_ref_drift_prevents_update(
    module: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, drift: str
) -> None:
    server = Server(module)

    async def ready_ci(*args: Any) -> int:
        return 80

    async def stale(*args: Any) -> None:
        if drift == "head":
            server.head = FOREIGN
        else:
            server.tip = FOREIGN

    monkeypatch.setattr(module.Fixture, "ready_ci", ready_ci)
    monkeypatch.setattr(module, "expect_stale", stale)
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.reply)) as client:
        with pytest.raises(module.AcceptanceError, match="identity, head, state, or base changed"):
            await module.submit(module.Fixture(client, server.scope, "submit"), tmp_path)
    assert [path for _, path, _ in server.writes] == ["/git/refs", "/contents/README.md", "/pulls"]
    assert not (tmp_path / "attempt.json").exists()


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("matching", ""),
        ("earlier_refusal", "fresh stale-CI refusal"),
        ("stale_snapshot_pin", "comparison pins changed"),
        ("missing_journal", "journal did not match"),
    ],
)
async def test_gate_requires_rule4_current_pins_and_matching_journal(
    module: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str, error: str
) -> None:
    scope = module.Scope(SOURCE, module.ANCESTOR, module.BASE, "123")
    verdict = {
        "verdict": "REFUSE",
        "head_sha": HEAD,
        "as_of": "2026-11-27T12:00:04Z",
        "exit_class": 3,
        "reasons": [{"code": "stale_ci", "detail": "same file changed"}],
    }
    if case == "earlier_refusal":
        verdict["reasons"] = [{"code": "main_red"}]
    comparison = SimpleNamespace(
        complete=True,
        attributes={
            "main_tip": FOREIGN if case == "stale_snapshot_pin" else scope.base,
            "head_sha": HEAD,
        },
    )

    async def gate(_client: object, name: str, **kwargs: Any) -> dict[str, Any]:
        assert (name, kwargs) == ("merge_gate", {"pr": PR})
        return verdict

    async def read_merge_gate(repo: str, number: int) -> SimpleNamespace:
        assert (repo, number) == (module.REPO, PR)
        return SimpleNamespace(comparison=comparison)

    async def events_after(*args: Any) -> list[SimpleNamespace]:
        if case == "missing_journal":
            return []
        return [
            SimpleNamespace(
                kind="core.merge_verdict",
                fields={**verdict, "number": PR, "as_of": "2026-11-27T12:00:00Z"},
            )
        ]

    @asynccontextmanager
    async def observer(
        root: Path, *, initialize: bool
    ) -> AsyncGenerator[tuple[object, SimpleNamespace]]:
        assert root == tmp_path
        assert initialize is True
        yield object(), SimpleNamespace(read_merge_gate=read_merge_gate, events_after=events_after)

    async def check_once(read: Any, predicate: Any, message: str) -> Any:
        value = await read()
        module.require(predicate(value), message)
        return value

    monkeypatch.setattr(module, "checked", gate)
    monkeypatch.setattr(module, "observer", observer)
    monkeypatch.setattr(module, "eventually", check_once)
    if error:
        with pytest.raises(module.AcceptanceError, match=error):
            await module.expect_stale(tmp_path, scope, PR, HEAD)
    else:
        await module.expect_stale(tmp_path, scope, PR, HEAD)
