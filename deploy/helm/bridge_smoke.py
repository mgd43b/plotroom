"""Check the Helm instance through an outside-kind bridge and a scripted local relay."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import tempfile
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path

import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

from deploy.bridge_smoke import DB_QUERY, tool, verify_bridge
from deploy.helm.smoke import consume_token_job, run

PROJECT = "bridge-fixture"
FIXTURE_HOST = "plotroom-fixture.example.test:8765"


@contextmanager
def port_forward(kube: list[str], service: str, directory: Path):
    log = directory / "port-forward.log"
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [*kube, "port-forward", "--address", "127.0.0.1", f"service/{service}", ":8765"],
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process.poll() is None:
                if bound := re.search(r"Forwarding from 127\.0\.0\.1:([0-9]+) ->", log.read_text()):
                    yield f"http://127.0.0.1:{bound[1]}"
                    return
                time.sleep(0.1)
            raise RuntimeError("could not forward the fixture Service on loopback")
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


async def check_hosts(base: str, operator: str) -> None:
    async with httpx2.AsyncClient(trust_env=False) as http:
        assert (await http.get(base + "/healthz")).status_code == 200
        for host in ("unlisted.example.test:8765", "plotroom-fixture.example.test:8766"):
            rejected = await http.get(base + "/healthz", headers={"Host": host})
            assert rejected.status_code == 421
        ready = await http.get(base + f"/readyz/{PROJECT}", headers={"Host": FIXTURE_HOST})
        assert ready.status_code == 200
    async with (
        httpx2.AsyncClient(
            trust_env=False, headers={"Authorization": f"Bearer {operator}", "Host": FIXTURE_HOST}
        ) as http,
        Client(streamable_http_client(base + "/mcp", http_client=http), mode="auto") as client,
    ):
        assert (await tool(client, "snapshot", {}))["project"] == PROJECT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--image-tag", required=True)
    args = parser.parse_args()
    if not args.context.startswith("kind-"):
        parser.error("an explicit disposable kind context is required")
    kube = [
        "kubectl",
        "--kubeconfig",
        args.kubeconfig,
        "--context",
        args.context,
        "--namespace",
        args.namespace,
    ]
    release = args.release
    board = f"{release}-board"
    project_settings = {"board": board, "workers": {"fixture": "fixture-peer"}}
    job, deployment = f"job/{release}-token", f"deployment/{release}"
    chart = str(Path(__file__).resolve().parent)
    with tempfile.TemporaryDirectory(prefix="plotroom-kind-bridge-") as temp:
        root = Path(temp)
        values = root / "values.json"
        values.write_text(
            json.dumps(
                {
                    "image": {"tag": args.image_tag},
                    "projects": [PROJECT],
                    "allowedHosts": [FIXTURE_HOST],
                    "bridge": {"projects": {PROJECT: project_settings}},
                    "tokenJob": {"enabled": True, "project": PROJECT, "role": "bridge"},
                }
            )
        )
        # A fresh namespace prevents accidental adoption or modification of user fixtures.
        run([*kube, "create", "namespace", args.namespace])
        run(
            [
                "helm",
                "install",
                release,
                chart,
                "--kubeconfig",
                args.kubeconfig,
                "--kube-context",
                args.context,
                "--namespace",
                args.namespace,
                "--values",
                str(values),
                "--wait",
                "--wait-for-jobs",
                "--timeout",
                "2m",
            ]
        )
        rendered = json.loads(run([*kube, "get", "configmap", release, "-o", "json"]))
        config = tomllib.loads(rendered["data"]["plotroom.toml"])
        assert config["projects"] == {
            PROJECT: {
                "switchboard": {"board": board, "via_bridge": True},
                "workers": {"fixture": {"switchboard_address": "fixture-peer"}},
            }
        }
        assert config["allowed_hosts"] == [
            "127.0.0.1:*",
            "localhost:*",
            f"{release}:8765",
            FIXTURE_HOST,
        ]
        run([*kube, "wait", "--for=condition=complete", job, "--timeout=90s"])
        bridge_token = consume_token_job(kube, deployment, job)
        operator = json.loads(
            run(
                [
                    *kube,
                    "exec",
                    deployment,
                    "--container",
                    "plotroom",
                    "--",
                    "plotroom",
                    "token",
                    "issue",
                    "--config",
                    "/etc/plotroom/plotroom.toml",
                    "--no-local",
                    "--project",
                    PROJECT,
                    "--role",
                    "orchestrator",
                ]
            )
        )["token"]

        def database(which, value):
            return run(
                [
                    *kube,
                    "exec",
                    deployment,
                    "--container",
                    "plotroom",
                    "--",
                    "python",
                    "-c",
                    DB_QUERY,
                    which,
                    value,
                ]
            ).strip()

        with port_forward(kube, release, root) as base:
            asyncio.run(check_hosts(base, operator))
            verify_bridge(root, board, base + "/mcp", operator, bridge_token, database)
    print(
        "Helm bridge smoke passed: rendered opt-in, exact Host scope, bridge Job, and outside-kind round trip."
    )


if __name__ == "__main__":
    main()
