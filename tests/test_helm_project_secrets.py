"""The chart renders credential references only, with mounts isolated to the server."""

import json
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

HELM = shutil.which("helm")
CHART = Path(__file__).resolve().parents[1] / "deploy" / "helm"
pytestmark = pytest.mark.skipif(HELM is None, reason="Helm is required to render the chart")

APP_REF = {"secretName": "fixture-app", "key": "mounted-key"}
PAT_REF = {"secretName": "fixture-pat", "key": "mounted-token"}
EMBEDDER_REF = {"secretName": "fixture-embedder", "key": "mounted-embedder"}
RAW = "fixture-unrenderable-credential"


def render(tmp_path: Path, values: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    assert HELM is not None
    values_file = tmp_path / "values.json"
    values_file.write_text(json.dumps(values), encoding="utf-8")
    return subprocess.run(
        [HELM, "template", "fixture", str(CHART), "-f", str(values_file)],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def resources(result: subprocess.CompletedProcess[str]) -> dict[str, dict[str, Any]]:
    assert result.returncode == 0, result.stderr
    documents = cast("list[dict[str, Any]]", list(yaml.safe_load_all(result.stdout)))
    assert all(doc["kind"] != "Secret" for doc in documents)
    return {doc["kind"]: doc for doc in documents}


def pod(resource: dict[str, Any]) -> dict[str, Any]:
    return resource["spec"]["template"]["spec"]


def config(resource: dict[str, Any]) -> dict[str, Any]:
    return tomllib.loads(resource["data"]["plotroom.toml"])


def mounts(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        mount["mountPath"]: mount
        for mount in cast("list[dict[str, Any]]", container["volumeMounts"])
    }


def secrets(values: dict[str, Any]) -> dict[str, Any]:
    return {"projects": ["app", "pat", "plain"], "projectSecrets": values}


def test_default_chart_has_no_project_secret_mounts(tmp_path: Path) -> None:
    rendered = resources(render(tmp_path, {}))
    server = pod(rendered["Deployment"])
    assert set(mounts(server["containers"][0])) == {"/etc/plotroom", "/data"}
    assert [volume["name"] for volume in server["volumes"]] == ["config", "data"]
    assert config(rendered["ConfigMap"])["projects"] == {"helm-fixture": {}}
    assert "Job" not in rendered


def test_projection_only_mounts_selected_keys_on_the_server(tmp_path: Path) -> None:
    prior_values = {
        "projects": ["app", "pat", "plain"],
        "bridge": {"projects": {"pat": {"board": "fixture", "workers": {"worker": "peer"}}}},
        "tokenJob": {"enabled": True, "project": "pat"},
    }
    prior = resources(render(tmp_path, prior_values))
    rendered = resources(
        render(
            tmp_path,
            {
                **prior_values,
                "projectSecrets": {
                    "app": {
                        "github": {
                            "repository": "owner/app",
                            "app": {"clientId": "Iv1.fixture", "installationId": 42, **APP_REF},
                        },
                        "embedder": {
                            "url": "https://example.invalid/embeddings",
                            "model": "fixture-model",
                            "dimension": 3,
                            **EMBEDDER_REF,
                        },
                    },
                    "pat": {"github": {"repository": "owner/pat", "pat": PAT_REF}},
                },
            },
        )
    )
    base = "/var/run/plotroom-secrets"
    prior_projects = config(prior["ConfigMap"])["projects"]
    assert config(rendered["ConfigMap"])["projects"] == {
        "app": {
            "github": {
                "repository": "owner/app",
                "app": {
                    "client_id": "Iv1.fixture",
                    "installation_id": 42,
                    "private_key_file": f"{base}/app/github-app.pem",
                },
            },
            "embedder": {
                "url": "https://example.invalid/embeddings",
                "model": "fixture-model",
                "dimension": 3,
                "key_file": f"{base}/app/embedder-key",
            },
        },
        "pat": {
            **prior_projects["pat"],
            "github": {"repository": "owner/pat", "token_file": f"{base}/pat/github-token"},
        },
        "plain": {},
    }
    for ref in (APP_REF, PAT_REF, EMBEDDER_REF):
        for value in ref.values():
            assert value not in rendered["ConfigMap"]["data"]["plotroom.toml"]
    server = pod(rendered["Deployment"])
    assert server["initContainers"] == pod(prior["Deployment"])["initContainers"]
    assert rendered["Job"] == prior["Job"]
    assert server["securityContext"]["fsGroup"] == 10001
    assert server["automountServiceAccountToken"] is False
    server_mounts = mounts(server["containers"][0])
    assert set(server_mounts) == {"/etc/plotroom", "/data", f"{base}/app", f"{base}/pat"}
    projections = {
        volume["name"]: volume["projected"]
        for volume in cast("list[dict[str, Any]]", server["volumes"])
        if "projected" in volume
    }
    assert len(projections) == 2
    for project, expected in (
        ("app", [(APP_REF, "github-app.pem"), (EMBEDDER_REF, "embedder-key")]),
        ("pat", [(PAT_REF, "github-token")]),
    ):
        mount = server_mounts[f"{base}/{project}"]
        assert mount["readOnly"] is True
        assert projections[mount["name"]] == {
            "defaultMode": 0o440,
            "sources": [
                {
                    "secret": {
                        "name": ref["secretName"],
                        "optional": False,
                        "items": [{"key": ref["key"], "path": filename}],
                    }
                }
                for ref, filename in expected
            ],
        }


@pytest.mark.parametrize(
    "settings",
    [
        {"undeclared": {"github": {"repository": "owner/repo", "pat": PAT_REF}}},
        {"pat": {}},
        {"pat": {"github": {"repository": "owner/repo"}}},
        {
            "pat": {
                "github": {
                    "repository": "owner/repo",
                    "pat": PAT_REF,
                    "app": {"clientId": "Iv1.fixture", "installationId": 42, **APP_REF},
                }
            }
        },
        {"pat": {"github": {"repository": "owner/repo", "pat": {"secretName": "fixture-pat"}}}},
        {
            "pat": {
                "github": {
                    "repository": "owner/repo",
                    "pat": {"secretName": "fixture-pat", "key": "../unexpected"},
                }
            }
        },
        {"pat": {"github": {"repository": "owner/repo", "pat": {**PAT_REF, "token": RAW}}}},
    ],
    ids=["undeclared", "empty", "no-auth", "mixed-auth", "missing-key", "invalid-key", "raw-token"],
)
def test_malformed_or_unscoped_credentials_fail_render(
    tmp_path: Path, settings: dict[str, Any]
) -> None:
    result = render(tmp_path, secrets(settings))
    assert result.returncode != 0
    assert RAW not in result.stdout
    assert RAW not in result.stderr
