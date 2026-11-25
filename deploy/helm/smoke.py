"""Install and exercise the chart on a disposable kind context supplied by the caller."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
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
        assert (await http.get(
            base + "/healthz", headers={"Host": "unlisted.example.test:8765"}
        )).status_code == 421
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


def consume_token_job(kube: list[str], deployment: str, job: str) -> str:
    assert not run([*kube, "logs", job]).strip()
    handoff = """
from pathlib import Path
import stat
path = Path("/data/.helm-bootstrap-token.json")
assert stat.S_IMODE(path.stat().st_mode) == 0o600
value = path.read_text()
path.unlink()
print(value)
"""
    token = json.loads(
        run(
            [
                *kube,
                "exec",
                deployment,
                "--container",
                "plotroom",
                "--",
                "python",
                "-c",
                handoff,
            ]
        )
    )["token"]
    run([*kube, "delete", job, "--wait=true"])
    return token


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

    # Reproduce the CI ordering deliberately. Adopt only the Service created
    # in this fresh fixture namespace, with the release's ownership metadata.
    run([*kube, "create", "namespace", args.namespace])
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": release,
            "labels": {"app.kubernetes.io/managed-by": "Helm"},
            "annotations": {
                "meta.helm.sh/release-name": release,
                "meta.helm.sh/release-namespace": args.namespace,
            },
        },
        "spec": {
            "selector": {
                "app.kubernetes.io/name": "plotroom",
                "app.kubernetes.io/instance": release,
                "app.kubernetes.io/component": "server",
            },
            "ports": [{"name": "http", "port": 8765, "targetPort": "http"}],
        },
    }
    run([*kube, "create", "-f", "-"], json.dumps(service))
    original_service = json.loads(run([*kube, "get", "service", release, "-o", "json"]))
    service_uid = original_service["metadata"]["uid"]
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
    assert spec["template"]["spec"]["enableServiceLinks"] is False
    installed_service = json.loads(run([*kube, "get", "service", release, "-o", "json"]))
    assert installed_service["metadata"]["uid"] == service_uid
    pvc = json.loads(run([*kube, "get", "pvc", f"{release}-data", "-o", "json"]))
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert pvc["status"]["phase"] == "Bound"
    run([*kube, "wait", "--for=condition=complete", job, "--timeout=90s"])
    token = consume_token_job(kube, deployment, job)

    def running_pod(previous_uid: str | None = None) -> tuple[str, str]:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            pods = json.loads(run([*kube, "get", "pods", "-l", selector, "-o", "json"]))["items"]
            ready = [
                pod
                for pod in pods
                if not pod["metadata"].get("deletionTimestamp")
                and any(
                    c["type"] == "Ready" and c["status"] == "True"
                    for c in pod["status"].get("conditions", [])
                )
            ]
            assert len(ready) <= 1, "more than one server pod is ready"
            if ready and ready[0]["metadata"]["uid"] != previous_uid:
                metadata = ready[0]["metadata"]
                return metadata["name"], metadata["uid"]
            time.sleep(0.25)
        raise TimeoutError("the replacement server pod did not become ready")

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
    run(
        [
            *kube,
            "exec",
            old_name,
            "--",
            "python",
            "-c",
            'import os; assert "PLOTROOM_PORT" not in os.environ',
        ]
    )
    first = check(old_name, create=True)
    run([*kube, "delete", "pod", old_name, "--wait=true", "--timeout=90s"])
    new_name, new_uid = running_pod(old_uid)
    assert new_uid != old_uid
    assert "previous data-directory owner has stopped" in run(
        [*kube, "logs", new_name, "-c", "wait-for-data-owner"]
    )
    latest = check(new_name, create=False)
    assert latest[1] == first[1]
    assert int(latest[0]) >= int(first[0])
    print(
        "Helm smoke passed: one ready replica, RWO storage, token Job and event retained after replacement."
    )


if __name__ == "__main__":
    main()
