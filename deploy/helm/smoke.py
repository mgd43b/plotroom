"""Install and exercise the chart on a disposable kind context supplied by the caller."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

CHECK_HTTP = """
import asyncio
import json
import sys

import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

fixture = json.load(sys.stdin)

async def check():
    base = fixture["base"]
    async with httpx2.AsyncClient(trust_env=False) as http:
        assert (await http.get(base + "/healthz")).status_code == 200
        ready = await http.get(base + "/readyz/" + fixture["project"])
        assert ready.status_code == 200
        assert ready.json()["projects"] == {fixture["project"]: "ready"}
    async with (
        httpx2.AsyncClient(
            headers={"authorization": "Bearer " + fixture["token"]}, trust_env=False,
        ) as http,
        Client(streamable_http_client(base + "/mcp", http_client=http), mode="auto") as client,
    ):
        async def tool(name, args):
            response = await client.call_tool(name, args)
            result = json.loads(response.content[0].text)
            assert result.get("ok"), name
            return result["data"]
        if fixture["create"]:
            await tool("decide", {
                "who": "Fixture operator",
                "exact_words": "Keep the Helm fixture event",
                "where_ref": {"kind": "chat", "ref": "helm-fixture"},
                "governs": [], "settles": [], "request_id": "helm-fixture-one",
            })
        board = await tool("snapshot", {})
        assert board["project"] == fixture["project"] and board["cursor"] > 0
        [decision] = (await tool("decisions", {}))["decisions"]
        assert decision["exact_words"] == "Keep the Helm fixture event"
        print(json.dumps([board["cursor"], decision["decision_id"]]))

asyncio.run(check())
"""


def run(command: list[str], stdin: str | None = None) -> str:
    return subprocess.check_output(command, input=stdin, text=True, timeout=180)


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
    project = "helm-fixture"
    deployment = f"deployment/{release}"
    job = f"job/{release}-token"
    selector = f"app.kubernetes.io/instance={release},app.kubernetes.io/component=server"
    chart = str(Path(__file__).resolve().parent)
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
            "--create-namespace",
            "--wait",
            "--wait-for-jobs",
            "--timeout",
            "2m",
            "--set",
            "tokenJob.enabled=true",
            "--set-string",
            f"image.tag={args.image_tag}",
        ]
    )
    spec = json.loads(run([*kube, "get", deployment, "-o", "json"]))["spec"]
    assert spec["replicas"] == 1 and spec["strategy"]["type"] == "Recreate"
    pvc = json.loads(run([*kube, "get", "pvc", f"{release}-data", "-o", "json"]))
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert pvc["status"]["phase"] == "Bound"
    run([*kube, "wait", "--for=condition=complete", job, "--timeout=90s"])
    token = json.loads(run([*kube, "logs", job]))["token"]
    # A completed fixture token Job no longer needs to retain the log containing its bearer.
    run([*kube, "delete", job, "--wait=true"])

    def running_pod() -> tuple[str, str]:
        run([*kube, "rollout", "status", deployment, "--timeout=90s"])
        [pod] = json.loads(run([*kube, "get", "pods", "-l", selector, "-o", "json"]))["items"]
        assert any(
            c["type"] == "Ready" and c["status"] == "True" for c in pod["status"]["conditions"]
        )
        return pod["metadata"]["name"], pod["metadata"]["uid"]

    def check(pod: str, *, create: bool) -> list[str | int]:
        data = {
            "base": f"http://{release}:8765",
            "project": project,
            "token": token,
            "create": create,
        }
        return json.loads(
            run([*kube, "exec", "-i", pod, "--", "python", "-c", CHECK_HTTP], json.dumps(data))
        )

    old_name, old_uid = running_pod()
    first = check(old_name, create=True)
    run([*kube, "delete", "pod", old_name, "--wait=true", "--timeout=90s"])
    new_name, new_uid = running_pod()
    assert new_uid != old_uid
    assert check(new_name, create=False) == first
    print(
        "Helm smoke passed: one ready replica, RWO storage, token Job and event retained after replacement."
    )


if __name__ == "__main__":
    main()
