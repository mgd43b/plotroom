import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def acceptance(monkeypatch: pytest.MonkeyPatch) -> Any:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(sys, "path", [str(root), *sys.path])
    return importlib.import_module("deploy.epic_two_acceptance")


@pytest.mark.parametrize("synthetic", [True, False])
def test_clean_witnesses_only_pass_in_synthetic_mode(
    acceptance: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    synthetic: bool,
) -> None:
    calls: list[str] = []

    def compose(tag: str) -> None:
        calls.append(tag)

    monkeypatch.setattr(acceptance, "local_checks", lambda: calls.append("pytest"))
    monkeypatch.setattr(acceptance, "compose_checks", compose)

    args = ["--image-tag", "fixture"]
    if synthetic:
        args.append("--synthetic")
    assert acceptance.main(args) == (0 if synthetic else 1)
    assert calls == ["pytest", "fixture"]
    output = capsys.readouterr().out
    assert [line[0] for line in output.splitlines() if line[:1].isdigit()] == list("123456789")
    assert output.count("Local synthetic PASS") == 9
    assert output.count("Compose synthetic PASS") == 5
    assert output.count("Full BLOCKED") == 6
    assert "CI fixture PAT" in output
    assert "approved CI App credential" in output
    assert "Existing recorded GitHub deployment" in output
    assert "Epic 2 Notes" in output
    assert "Integrated credential-backed CI acceptance remains pending" in output
    summary = "remains incomplete" if synthetic else "full acceptance blocked"
    assert summary in output


@pytest.mark.parametrize("failed", ["local_checks", "compose_checks"])
def test_failed_environment_stays_unverified(
    acceptance: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed: str,
) -> None:
    def fail(*_args: str) -> None:
        raise subprocess.CalledProcessError(1, "fixture-check")

    def compose(_tag: str) -> None:
        pass

    monkeypatch.setattr(acceptance, "local_checks", lambda: None)
    monkeypatch.setattr(acceptance, "compose_checks", compose)
    monkeypatch.setattr(acceptance, failed, fail)
    assert acceptance.main(["--image-tag", "fixture", "--synthetic"]) == 1
    captured = capsys.readouterr()
    environment = "Local" if failed == "local_checks" else "Compose"
    assert environment in captured.err
    assert "FAILED (CalledProcessError)" in captured.err
    assert f"{environment} synthetic UNVERIFIED" in captured.out
    assert f"{environment} synthetic PASS" not in captured.out
    assert "Full BLOCKED" in captured.out


@pytest.mark.parametrize("fault", [None, "skipped", "wrong-identity"])
def test_local_checks_require_every_exact_witness(
    acceptance: Any, monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    def pytest_run(command: list[str], **_kwargs: object) -> None:
        report = Path(
            next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml="))
        )
        cases: list[str] = []
        for selector in acceptance.WITNESSES:
            filename, name = selector.split("::")
            status = ""
            if selector == acceptance.WITNESSES[-1]:
                if fault == "wrong-identity":
                    name = "unrelated_passing_test"
                elif fault == "skipped":
                    status = "<skipped />"
            cases.append(
                f'<testcase classname="tests.{Path(filename).stem}" name="{name}">'
                f"{status}</testcase>"
            )
        report.write_text(
            "<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>",
            encoding="utf-8",
        )

    monkeypatch.setattr(acceptance.subprocess, "run", pytest_run)
    if fault is None:
        acceptance.local_checks()
        return
    with pytest.raises(RuntimeError, match="local witnesses did not run to completion"):
        acceptance.local_checks()


def test_spike_finding_must_be_in_epic_notes(
    acceptance: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    epic = tmp_path / "epic.md"
    epic.write_text(
        "## Notes\n\n## Other\n- Finding (entry 1, #31, date): read-only SessionReader.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(acceptance, "EPIC_NOTES", epic)
    with pytest.raises(RuntimeError, match="spike answer is missing"):
        acceptance.local_checks()
