"""Run the repeatable Epic 2 acceptance witnesses with synthetic inputs."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from deploy.bridge_smoke import ROOT, run, smoke
from deploy.epic_two_archived import archived_checks
from deploy.epic_two_mirrors import work_item_mirrors

WITNESSES = (
    "test_github.py::test_pull_lifecycle_and_credentials_preserve_committed_state",
    "test_github.py::test_on_demand_daemon_reads_the_mounted_pat",
    "test_github_details.py::test_github_transitions_and_pagination_are_durable",
    "test_switchboard.py::test_switchboard_redelivery_after_interrupted_ack",
    "test_intake.py::test_worker_ingest_wakes_local_watch_and_ack_closes[None]",
    "test_intake.py::test_worker_ingest_wakes_local_watch_and_ack_closes[2]",
    "test_github_app.py::test_app_daemon_keeps_polling_across_short_installation_tokens",
    "test_work_items_github_tree.py::test_default_branch_tick_matches_checkout_and_preserves_last_good_view",
    "test_github_details.py::test_github_issues_flow_through_the_runtime_tick",
    "test_work_items_github_issues.py::test_work_items_match_the_synced_bmad_board",
    "test_deploy_status.py::test_paginated_get_only_deployments_isolate_failures_and_deduplicate",
    "test_deploy_status.py::test_only_own_lane_worker_can_report_and_replay[http]",
    "test_deploy_status.py::test_only_own_lane_worker_can_report_and_replay[stdio]",
)
EPIC_NOTES = (
    ROOT
    / "_bmad-output/initiative-orchestration-control-plane/tickets/epic-one-inbox"
    / "epic-one-inbox.md"
)

# In the order of the nine Done-when clauses in epic-one-inbox.md.
CRITERIA = (
    (
        "Merged PR, CI completion and review thread recorded and projected once",
        "Mounted-PAT daemon startup, poller lifecycle, event deduplication and projection",
        None,
        "Real test-repository PR, CI and thread identities in this suite; CI fixture PAT.",
    ),
    (
        "Codex switchboard delivery and upstream acknowledgment after commit",
        "Scripted relay redelivery after an interrupted acknowledgment",
        "Bridge commit before upstream ack, redelivery and outbound send",
        "Bridge delivery against a hosted instance.",
    ),
    (
        "Claude turn report stored once after two ingest calls",
        "Worker replay with and without a turn index",
        "Authenticated worker replay and one SQLite event",
        None,
    ),
    (
        "Session-reader spike answer recorded and R26 met via ingest",
        "Turn report and session message ingest; spike answer checked in Epic 2 Notes",
        "Authenticated turn report and session message ingest through the daemon",
        None,
    ),
    (
        "Hosted GitHub App polling across refresh with credentials kept out of records and logs",
        "Daemon polling across synthetic short-lived installation tokens with leakage checks",
        None,
        "Hosted App run across a real refresh using the approved CI App credential.",
    ),
    (
        "BMad tree and BMad-shaped GitHub Issues reflected read-only from the test repository",
        "Default-branch tree parity, Issues runtime and board parity",
        None,
        "Real BMad-shaped Issues and tree on the same test repository; CI fixture PAT.",
    ),
    (
        "GitHub deployment and owned lane deploy report recorded once each",
        "GET-only deployment polling, deduplication and owned-worker replay",
        None,
        "Existing recorded GitHub deployment and owned lane report in the real-fixture run.",
    ),
    (
        "Inbound worker message wakes plotroom watch",
        "Armed CLI waiter wakes when a worker report commits",
        "Armed MCP waiter wakes; CLI returns the same item from a pre-event cursor",
        None,
    ),
    (
        "Lane inferred from PR branch and switchboard sender address",
        "Synthetic GitHub branch and switchboard sender attribution",
        "Durable event names the lane inferred from the configured sender address",
        "Live projected GitHub branch and PR identity on the test repository.",
    ),
)


def local_checks() -> None:
    _, notes, finding_section = EPIC_NOTES.read_text(encoding="utf-8").partition("\n## Notes\n")
    findings = finding_section.split("\n## ", 1)[0].splitlines()
    if not notes or not any(
        line.startswith("- Finding (entry 1, #31,")
        and "read-only" in line
        and "SessionReader" in line
        for line in findings
    ):
        raise RuntimeError("the entry 1 spike answer is missing from Epic 2 Notes")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PLOTROOM_", "PYTEST_"))
    }
    with tempfile.TemporaryDirectory(prefix="plotroom-epic-two-pytest-") as directory:
        report = Path(directory) / "witnesses.xml"
        subprocess.run(  # noqa: S603 - fixed interpreter and test selection, no shell
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                f"--junitxml={report}",
                *(f"tests/{node}" for node in WITNESSES),
            ],
            cwd=ROOT,
            check=True,
            timeout=300,
            env=env,
        )
        cases = ElementTree.parse(report).findall(".//testcase")  # noqa: S314 - owned pytest report
        passed = {
            f"{case.get('classname', '').rsplit('.', 1)[-1]}.py::{case.get('name')}"
            for case in cases
            if all(case.find(verdict) is None for verdict in ("skipped", "error", "failure"))
        }
        if passed != set(WITNESSES) or len(cases) != len(WITNESSES):
            raise RuntimeError("one or more local witnesses did not run to completion")


def compose_checks(tag: str) -> None:
    endpoint = os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None
    if endpoint is None:
        endpoint = run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
        ).strip()
    if not endpoint.startswith("unix://"):
        raise RuntimeError("the Compose witness requires a local Docker Unix socket")
    with tempfile.TemporaryDirectory(prefix="plotroom-epic-two-") as directory:
        smoke(Path(directory), f"plotroom-epic-two-{uuid4().hex[:12]}", tag)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image-tag", required=True, help="tag of an existing local Plotroom image"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--synthetic", action="store_true", help="allow success for the synthetic checks alone"
    )
    modes.add_argument(
        "--archived-github",
        action="store_true",
        help="also witness archived terminal facts using the existing read-only gh session",
    )
    modes.add_argument(
        "--work-item-mirrors",
        action="store_true",
        help="also verify the pinned fixture's read-only local work-item sources",
    )
    parser.add_argument("--milestone", type=int, help="verified fixture milestone number")
    parser.add_argument("--child", type=int, help="verified fixture story issue number")
    args = parser.parse_args(argv)
    numbers = (args.milestone, args.child)
    if any(number is not None for number in numbers) and (
        not args.work_item_mirrors
        or any(number is None or not 0 < number < 2**63 for number in numbers)
    ):
        parser.error("--milestone and --child require --work-item-mirrors and two positive numbers")
    if not __debug__:
        parser.error("the acceptance witnesses require Python assertions to be enabled")
    print("Running Epic 2 synthetic witnesses. Full acceptance needs real evidence.", flush=True)

    def check(label: str, run_check: Callable[[], None], *, verdict: str = "passed") -> bool:
        try:
            run_check()
        except Exception as error:
            print(f"{label} FAILED ({type(error).__name__}): {error}", file=sys.stderr, flush=True)
            if isinstance(error, ExceptionGroup):
                traceback.print_exception(error)
            return False
        print(f"{label} {verdict}.", flush=True)
        return True

    local = check("Local daemon and adapter tests", local_checks)
    compose = check("Disposable Compose smoke", lambda: compose_checks(args.image_tag))
    archived = args.archived_github and check(
        "Archived GitHub evidence", lambda: asyncio.run(archived_checks(args.image_tag, print))
    )
    mirror_complete = False

    def check_mirrors() -> None:
        nonlocal mirror_complete
        mirror_complete = asyncio.run(
            work_item_mirrors(args.image_tag, args.milestone, args.child, print)
        )

    mirrors_checked = args.work_item_mirrors and check(
        "Local and Compose work-item mirror observations", check_mirrors, verdict="completed"
    )
    for number, (criterion, local_witness, compose_witness, blocked) in enumerate(CRITERIA, 1):
        print(f"{number}. {criterion}")
        print(f"   Local synthetic {'PASS' if local else 'UNVERIFIED'} — {local_witness}")
        if mirrors_checked and number == 6:
            print("   Compose work-item evidence replays captured provider input.")
        elif compose_witness is None:
            print("   Compose — not exercised by this runner")
        else:
            print(f"   Compose synthetic {'PASS' if compose else 'UNVERIFIED'} — {compose_witness}")
        if archived and number in (1, 9):
            print(
                "   Archived GitHub PASS — terminal facts in the local daemon and isolated Compose"
            )
            print("   Original live transitions remain outside this terminal-fact witness.")
            print("   Full PENDING — verify in the integrated credential-backed CI run.")
        elif mirror_complete and number == 6:
            print("   Full PENDING — verify both sources in integrated credential-backed CI.")
        elif blocked:
            print(f"   Full BLOCKED — {blocked}")
    print("Integrated credential-backed CI acceptance remains pending.")

    if not local or not compose:
        print("Result: synthetic checks failed. Full acceptance remains incomplete.")
        return 1
    if args.archived_github and not archived:
        print("Result: archived GitHub checks failed. Full acceptance remains incomplete.")
        return 1
    if args.work_item_mirrors and not mirrors_checked:
        print("Result: work-item mirror checks failed. Full acceptance remains incomplete.")
        return 1
    if not args.synthetic:
        covered = (
            "Synthetic and archived GitHub checks passed."
            if archived
            else "Synthetic checks passed."
        )
        print(f"Result: full acceptance blocked. {covered}")
        return 1
    print("Result: synthetic checks passed. Full acceptance remains incomplete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
