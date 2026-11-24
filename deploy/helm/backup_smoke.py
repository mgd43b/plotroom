"""Verify CronJob backups using a fresh namespace in an explicit local kind cluster."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from smoke import CHECK_HTTP, run

CHECK_BACKUPS = """
import asyncio
import shutil
import sqlite3
import stat
import tempfile
from contextlib import closing
from pathlib import Path

from plotroom.adapters.store_sqlite import SqliteStore, head_revision


async def check():
    for project in ("alpha", "beta"):
        source = Path("/backups") / project
        copies = sorted(source.glob("scheduled-*.bak"))
        assert len(copies) == 2
        for copy in copies:
            assert stat.S_IMODE(copy.stat().st_mode) == 0o600
            assert not copy.with_name(copy.name + "-wal").exists()
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                store = SqliteStore(project, directory)
                shutil.copyfile(copy, store.path)
                try:
                    await store.migrate()
                    assert await store.revision() == head_revision()
                finally:
                    await store.close()
                with closing(sqlite3.connect(store.path)) as db:
                    assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                    assert db.execute(
                        "SELECT count(*) FROM events WHERE kind='core.decide' AND instr(fields, ?)",
                        ("Keep the Helm fixture event",),
                    ).fetchone() == (1,)


asyncio.run(check())
"""


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
    chart = str(Path(__file__).resolve().parent)
    secret = f"{release}-backup-token"
    deployment = f"deployment/{release}"
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
            "--timeout",
            "2m",
            "--set",
            "backup.enabled=true,backup.suspend=true,backup.retain=2",
            "--set",
            "projects={alpha,beta}",
            "--set-string",
            f"image.tag={args.image_tag}",
            "--set-string",
            f"backup.tokenSecret={secret}",
        ]
    )
    volumes = [
        json.loads(run([*kube, "get", "pvc", f"{release}-{purpose}", "-o", "json"]))["spec"][
            "volumeName"
        ]
        for purpose in ("data", "backups")
    ]
    assert len(set(volumes)) == 2
    tokens = {}
    for project in ("alpha", "beta"):
        token = json.loads(
            run(
                [
                    *kube,
                    "exec",
                    deployment,
                    "--",
                    "plotroom",
                    "token",
                    "issue",
                    "--config",
                    "/etc/plotroom/plotroom.toml",
                    "--no-local",
                    "--project",
                    project,
                    "--role",
                    "orchestrator",
                ]
            )
        )["token"]
        fixture = {
            "base": f"http://{release}:8765",
            "project": project,
            "token": token,
            "create": True,
        }
        run(
            [*kube, "exec", "-i", deployment, "--", "python", "-c", CHECK_HTTP], json.dumps(fixture)
        )
        tokens[project] = base64.b64encode(token.encode()).decode()
    run(
        [*kube, "create", "-f", "-"],
        json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": secret},
                "data": tokens,
            }
        ),
    )
    [cron] = json.loads(run([
        *kube, "get", "cronjobs", "-l", f"app.kubernetes.io/instance={release}", "-o", "json"
    ]))["items"]
    cron_name = cron["metadata"]["name"]
    for index in range(3):
        name = f"{release}-copy-{index}"
        run([*kube, "create", "job", f"--from=cronjob/{cron_name}", name])
        run([*kube, "wait", "--for=condition=complete", f"job/{name}", "--timeout=120s"])
    run([*kube, "exec", deployment, "--", "python", "-c", CHECK_BACKUPS])
    run([
        "helm", "upgrade", release, chart, "--kubeconfig", args.kubeconfig,
        "--kube-context", args.context, "--namespace", args.namespace,
        "--wait", "--timeout", "2m", "--reuse-values", "--set", "backup.enabled=false",
    ])
    remaining = json.loads(run([*kube, "get", "pvc", f"{release}-backups", "-o", "json"]))
    assert remaining["spec"]["volumeName"] == volumes[1]
    print(
        "CronJob smoke passed: separate claim, two retained consistent and reopenable backups per project."
    )


if __name__ == "__main__":
    main()
