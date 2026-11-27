"""Exercise the workflow's Bash guards with stubbed Git and uv."""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/github-fixture-acceptance.yml"
ROW_SIX_WORKFLOW = WORKFLOW.with_name("github-fixture-row-six.yml")
SHA = "a" * 40
OVERRIDE = "PLOTROOM_GITHUB_FIXTURE_GITHUB_TOKEN"


@pytest.fixture
def steps() -> dict[str, str]:
    workflow = cast("dict[str, Any]", yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader))
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]
    assert dispatch["phase"]["default"] == "preflight"
    job_guard = workflow["jobs"]["fixture"]["if"]
    assert "github.actor == 'mgd43b'" in job_guard
    assert "github.triggering_actor == 'mgd43b'" in job_guard
    return {
        step["name"]: step["run"] for step in workflow["jobs"]["fixture"]["steps"] if "run" in step
    }


@pytest.fixture
def row_six_steps() -> dict[str, str]:
    workflow = cast(
        "dict[str, Any]", yaml.load(ROW_SIX_WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == {"expected_sha"}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"row-six"}
    job = workflow["jobs"]["row-six"]
    assert job["if"].split(" && ") == [
        "github.server_url == 'https://github.com'",
        "github.repository == 'mgd43b/plotroom'",
        "github.ref == 'refs/heads/main'",
        "github.actor == 'mgd43b'",
        "github.triggering_actor == 'mgd43b'",
    ]
    assert job["timeout-minutes"] == "15"
    assert job["steps"][0]["with"] == {
        "ref": "${{ github.sha }}",
        "persist-credentials": "false",
    }
    [secret_step] = [step for step in job["steps"] if "secrets." in str(step.get("env", {}))]
    assert secret_step["name"] == "Verify published work-item mirrors"
    assert secret_step["env"] == {"FIXTURE_PAT": f"${{{{ secrets.{OVERRIDE} }}}}"}
    assert job["steps"][-1] == secret_step
    return {step["name"]: step["run"] for step in job["steps"] if "run" in step}


def run_step(script: str, stub_bin: Path, **values: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        env={"PATH": f"{stub_bin}:{os.environ['PATH']}", **values},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, 0),
        ({"EXPECTED_SHA": "not-a-sha"}, 1),
        ({"GITHUB_SHA": "b" * 40}, 1),
        ({"MOCK_SHA": "b" * 40}, 1),
        ({"MOCK_MAIN": "b" * 40}, 1),
        ({"MOCK_DIRTY": "yes"}, 1),
    ],
)
@pytest.mark.parametrize("workflow_fixture", ["steps", "row_six_steps"])
def test_workflow_checks_reviewed_main_source(
    request: pytest.FixtureRequest,
    workflow_fixture: str,
    tmp_path: Path,
    overrides: dict[str, str],
    expected: int,
) -> None:
    workflow_steps = cast("dict[str, str]", request.getfixturevalue(workflow_fixture))
    git = tmp_path / "git"
    git.write_text(
        """#!/bin/bash
set -eu
case "$1" in
  rev-parse) printf '%s\\n' "$MOCK_SHA" ;;
  diff) test "$MOCK_DIRTY" = no ;;
  ls-remote)
    test "$*" = 'ls-remote --exit-code https://github.com/mgd43b/plotroom.git refs/heads/main'
    printf '%s\\trefs/heads/main\\n' "$MOCK_MAIN"
    ;;
  *) exit 3 ;;
esac
"""
    )
    git.chmod(0o700)
    env = {
        "EXPECTED_SHA": SHA,
        "GITHUB_SHA": SHA,
        "MOCK_SHA": SHA,
        "MOCK_MAIN": SHA,
        "MOCK_DIRTY": "no",
        "GITHUB_RUN_ATTEMPT": "1",
        **overrides,
    }
    result = run_step(workflow_steps["Verify the reviewed source"], tmp_path, **env)
    assert result.returncode == expected, result.stderr


def test_row_six_source_guard_rejects_reruns(row_six_steps: dict[str, str], tmp_path: Path) -> None:
    result = run_step(
        row_six_steps["Verify the reviewed source"],
        tmp_path,
        EXPECTED_SHA=SHA,
        GITHUB_SHA=SHA,
        GITHUB_RUN_ATTEMPT="2",
    )
    assert result.returncode == 1
    assert not result.stderr


@pytest.mark.parametrize(
    ("credential", "harness_exit", "expected"),
    [("test-marker", 0, 0), ("test-marker", 13, 13), ("", 0, 1)],
)
def test_row_six_workflow_confines_fixture_credential(
    row_six_steps: dict[str, str],
    tmp_path: Path,
    credential: str,
    harness_exit: int,
    expected: int,
) -> None:
    mktemp_path = shutil.which("mktemp")
    assert mktemp_path is not None
    mktemp = tmp_path / "mktemp"
    mktemp.write_text(
        "#!/bin/sh\n"
        'test "${FIXTURE_PAT+x}${fixture_pat+x}${PLOTROOM_GITHUB_FIXTURE_GITHUB_TOKEN+x}" = ""'
        " || exit 19\n"
        f'exec {shlex.quote(mktemp_path)} "$@"\n'
    )
    mktemp.chmod(0o700)
    probe = tmp_path / "probe.py"
    probe.write_text(
        """import os
import sys
from pathlib import Path

args = sys.argv[1:]
assert args[:-1] == [
    "run", "--locked", "--offline", "--no-dev", "python", "-m",
    "deploy.epic_two_mirrors", "--verify-published", "--image-tag", "row-six-123",
    "--token-file",
]
assert "FIXTURE_PAT" not in os.environ
assert "fixture_pat" not in os.environ
assert "PLOTROOM_GITHUB_FIXTURE_GITHUB_TOKEN" not in os.environ
assert "test-marker" not in args
token = Path(args[-1])
assert token.name == "github-token"
assert token.parent.parent == Path(os.environ["RUNNER_TEMP"])
assert token.parent.stat().st_mode & 0o777 == 0o700
assert token.stat().st_mode & 0o777 == 0o600
assert token.read_text() == "test-marker\\n"
sys.exit(int(os.environ["HARNESS_EXIT"]))
"""
    )
    stub = tmp_path / "uv"
    stub.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(probe))} "$@"\n'
    )
    stub.chmod(0o700)
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    result = run_step(
        row_six_steps["Verify published work-item mirrors"],
        tmp_path,
        RUNNER_TEMP=str(runner_temp),
        FIXTURE_PAT=credential,
        GITHUB_RUN_ID="123",
        HARNESS_EXIT=str(harness_exit),
        **{OVERRIDE: "synthetic-override"},
    )
    assert result.returncode == expected, result.stderr
    assert not result.stdout
    assert not result.stderr
    assert not tuple(runner_temp.iterdir())


@pytest.mark.parametrize(
    ("phase", "attempt", "credential", "harness_exit", "expected"),
    [
        ("preflight", "1", "synthetic", 0, 0),
        ("acceptance", "1", "synthetic", 0, 0),
        ("acceptance", "2", "synthetic", 0, 1),
        ("unknown", "1", "synthetic", 0, 2),
        ("preflight", "1", "", 0, 1),
        ("preflight", "1", "synthetic", 15, 15),
    ],
)
def test_workflow_confines_fixture_credential(
    steps: dict[str, str],
    tmp_path: Path,
    phase: str,
    attempt: str,
    credential: str,
    harness_exit: int,
    expected: int,
) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        """import os
import sys
from pathlib import Path

args = sys.argv[1:]
entry = args.index("scripts/github_fixture_acceptance.py")
phase_flag, phase, directory_flag, directory = args[entry + 1:]
assert phase_flag == "--phase" and phase == os.environ["FIXTURE_PHASE"]
assert directory_flag == "--directory"
assert "FIXTURE_PAT" not in os.environ and "PLOTROOM_GITHUB_FIXTURE_GITHUB_TOKEN" not in os.environ
assert "synthetic" not in args
private_dir = Path(directory)
assert private_dir.parent == Path(os.environ["RUNNER_TEMP"])
assert private_dir.stat().st_mode & 0o777 == 0o700
token = private_dir / "github-token"
assert token.stat().st_mode & 0o777 == 0o600
assert token.read_text() == "synthetic\\n"
sys.exit(int(os.environ["HARNESS_EXIT"]))
"""
    )
    stub = tmp_path / "uv"
    stub.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(probe))} "$@"\n'
    )
    stub.chmod(0o700)
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    result = run_step(
        steps["Verify the dedicated GitHub fixture"],
        tmp_path,
        RUNNER_TEMP=str(runner_temp),
        FIXTURE_PHASE=phase,
        GITHUB_RUN_ATTEMPT=attempt,
        FIXTURE_PAT=credential,
        HARNESS_EXIT=str(harness_exit),
        **{OVERRIDE: "synthetic-override"},
    )
    assert result.returncode == expected, result.stderr
    assert not result.stdout
    assert not result.stderr
    assert not tuple(runner_temp.iterdir())
