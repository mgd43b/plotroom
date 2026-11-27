# Running Plotroom

How to run Plotroom from a checkout: as a local daemon, with Docker Compose, on a local kind cluster with the Helm chart, and with the Switchboard bridge. Back to the [README](../README.md).

## Run your own local instance

Plotroom runs from source as a local daemon with a SQLite ledger per project.
Save this one-worker example as `plotroom.toml` next to the checkout. The
relative `data_dir` keeps the ledger alongside the config, outside the
checkout.

```toml
data_dir = "plotroom-data"

[projects.example.workers.agent]
```

From the checkout, run `uv run --locked plotroom serve --config ../plotroom.toml` in one
terminal. In a second terminal, from the same checkout, issue a project-scoped token for each role.

```sh
uv run --locked plotroom token issue --config ../plotroom.toml --project example --role orchestrator
uv run --locked plotroom token issue --config ../plotroom.toml --project example --role worker:agent
```

Configure each MCP client to launch the stdio proxy using the
[checkout and config paths in the README](../README.md#run-your-own). Use the `token_id`
returned for that client's role.

On macOS and Linux, `plotroom connect --stdio` with a config-derived URL uses a private Unix socket
to verify the local daemon before sending credentials or MCP requests. Restart any `plotroom serve`
process that was already running before upgrading so it can publish this socket. Explicit URLs
continue to use HTTP or HTTPS as configured.

For an explicit HTTPS URL with a bearer token, Plotroom verifies the certificate chain and hostname.
Set `SSL_CERT_FILE` to a PEM CA bundle or `SSL_CERT_DIR` to an OpenSSL hashed CA directory to
configure trust. These bearer connections go directly to the endpoint and bypass environment proxies.

Hosted container and Kubernetes setup are tracked in the project plan.
See the [local server and ledger design](../_bmad-output/initiative-orchestration-control-plane/tickets/epic-server-and-ledger/epic-server-and-ledger.md)
and the [architecture notes](../_bmad-output/initiative-orchestration-control-plane/architecture-orchestration-control-plane/architecture-orchestration-control-plane.md).

## Run locally with Docker Compose

From the checkout, choose a Compose project name for this instance. Compose
builds the image and mounts a named volume at `/data` for the project databases
and the hashed instance token registry. The HTTP port is published on loopback.

```sh
export PLOTROOM_PROJECTS=compose-demo
docker compose -p plotroom-local up --build --detach --wait
curl --fail http://127.0.0.1:8765/healthz
curl --fail http://127.0.0.1:8765/readyz/compose-demo
docker compose -p plotroom-local run --rm --no-deps -T plotroom \
  token issue --no-local --project compose-demo --role orchestrator
```

Store the returned token privately. Configure an MCP HTTP client with
`http://127.0.0.1:8765/mcp` and send the token as a bearer. Call `snapshot` to
check the project. In a disposable instance, call `decide` with these arguments.

```json
{
  "who": "Fixture operator",
  "exact_words": "Compose restart fixture",
  "where_ref": {"kind": "chat", "ref": "compose-fixture"},
  "governs": [],
  "settles": [],
  "request_id": "compose-fixture-one"
}
```

Restart the service, then use the same bearer to call `snapshot` and `decisions`.
The project and decision should still be present. The event is stored in the volume.

```sh
docker compose -p plotroom-local restart plotroom
docker compose -p plotroom-local up --detach --wait --no-build
curl --fail http://127.0.0.1:8765/readyz/compose-demo
```

Keep the project name and configuration consistent between commands. Set
`PLOTROOM_HTTP_PORT` before the first `up` to use a different host port, or set it
to `0` and use `docker compose -p plotroom-local port plotroom 8765` to look up the assigned port.
`GET /healthz` shows whether HTTP is running. `GET /readyz` checks all configured
projects, and `GET /readyz/<project>` checks one. A project that cannot serve its
ledger returns HTTP 503 on its readiness endpoint. Both probes apply the Host
allowlist. MCP calls use a project-scoped bearer.

An existing project is backed up with SQLite's online backup API before its
schema changes. Backups are private files under `migration-backups` in the data
directory, alongside the project databases. A restart at the current revision
creates no new backup.

If a backup fails or exceeds its 60-second deadline, or if a project file is
corrupt or stamped with a newer schema,
that project's readiness reports unavailable. Healthy projects remain usable.
Inspect `docker compose -p plotroom-local logs plotroom` for the project and reason. Compose's
container health check uses `/healthz`, so it can stay healthy while an individual
project's `/readyz` endpoint returns 503. To restore a backup, stop the daemon
first and preserve the current database and its SQLite sidecars before replacing
it. Use a Plotroom version that supports the backup's schema.

## Run the Helm chart in local kind

Use a disposable cluster and an explicit kubeconfig. Build and load the image
locally before installing. From the checkout, the smoke helper installs the
chart, issues a fixture token, verifies the Service and project readiness, and
deletes the server pod. It checks that the replacement serves the same decision
with the same token.

```sh
cfg=$(mktemp -d)
cluster="plotroom-local-$(basename "$cfg" | tr '[:upper:].' '[:lower:]-')"
export KUBECONFIG="$cfg/config"
docker build --tag "plotroom:$cluster" .
kind create cluster --name "$cluster" --kubeconfig "$KUBECONFIG" --wait 120s
kind load docker-image "plotroom:$cluster" --name "$cluster"
helm lint deploy/helm --set tokenJob.enabled=true
helm template plotroom deploy/helm --set tokenJob.enabled=true
python3 deploy/helm/smoke.py --kubeconfig "$KUBECONFIG" --context "kind-$cluster" \
  --namespace plotroom-fixture --release plotroom --image-tag "$cluster"
```

The chart keeps the SQLite databases and instance token hashes in one
ReadWriteOnce claim. A replacement waits for the previous daemon to release
the data-directory lock before starting. Run the optional token Job against the same claim for
local verification. The Job leaves its bearer in a temporary 0600 file on
the claim. The smoke helper consumes the file without logging it and removes
the completed Job. To remove the entire disposable cluster, run
`kind delete cluster --name "$cluster" --kubeconfig "$KUBECONFIG"`.

### Mount project credentials

Create the referenced Kubernetes Secrets in the release namespace before installing
the chart. Supply Secret names and key names in the chart values.

```yaml
projects: [app-project, pat-project]
projectSecrets:
  app-project:
    github:
      repository: owner/repository
      app:
        clientId: Iv1.example
        installationId: 123456
        secretName: app-credentials
        key: app-key
    embedder:
      url: https://embedding.example.com/v1/embeddings
      model: example-model
      dimension: 384
      secretName: search-credentials
      key: api-key
  pat-project:
    github:
      repository: owner/another-repository
      pat:
        secretName: pat-credentials
        key: token
```

Each binding selects a key and projects it as a read-only file below
`/var/run/plotroom-secrets/<project>/`. The App key uses `github-app.pem`, the PAT
uses `github-token`, and the embedder key uses `embedder-key`. The chart places
the corresponding file paths in Plotroom's TOML. A missing Secret or selected
key prevents Kubernetes from starting the server pod. Helm rejects undeclared
projects, malformed mappings, and a GitHub binding that supplies both App and PAT.

Use an HTTPS embedding endpoint for a mounted bearer key. The server reads the
selected file for each embedding request. Keep private values in the Secrets
and use public settings in the chart values and endpoint URL.

### Verify scheduled backups locally

On the same disposable cluster, run the backup smoke helper in a fresh
namespace. It configures two projects and a separate backup claim, issues
fixture orchestrator tokens, and starts three Jobs from the suspended
CronJob's spec. It verifies the configured retention and opens each retained
copy through the SQLite migration entry point.

```sh
helm lint deploy/helm --set backup.enabled=true,backup.tokenSecret=fixture-token
python3 deploy/helm/backup_smoke.py --kubeconfig "$KUBECONFIG" --context "kind-$cluster" \
  --namespace backup-fixture --release backups --image-tag "$cluster"
```

With `backup.enabled` set, the server mounts the backup volume at `/backups`.
The server requires the configured backup directory to exist and be writable.
The scheduler mounts an existing Secret whose keys are the configured project
names and whose values are their orchestrator bearers. Each request backs up
the project identified by that bearer. The scheduler does not mount either
database or backup volume. Disabling it leaves the backup claim in place.
Delete the disposable cluster when finished.

### Verify the chart's local client and bridge configuration

Reuse the locally built image in the disposable kind cluster. The fixture
installs into a fresh namespace, reads the rendered TOML, consumes a private
bridge token handoff from the Job, and forwards the Service only on loopback.
It checks a bearer-authenticated snapshot with the configured Host, rejection
of unlisted Hosts and ports, and a scripted relay round trip through a bridge
process running outside kind.

```sh
uv run --locked python -m deploy.helm.bridge_smoke --kubeconfig "$KUBECONFIG" \
  --context "kind-$cluster" --namespace bridge-fixture --release bridge --image-tag "$cluster"
```

For a disposable client-acceptance install, use values such as these with the
locally loaded image tag. Each project listed in `bridge.projects` must also
appear in `projects`. That entry sets `via_bridge = true` and maps the selected
worker names to their exact relay addresses. Projects without a bridge entry
keep the existing behavior.

```yaml
projects: [bridge-fixture]
allowedHosts: [plotroom-fixture.example.test:8765]
bridge:
  projects:
    bridge-fixture:
      board: bridge-fixture-board
      workers:
        fixture: fixture-peer
tokenJob:
  enabled: true
  project: bridge-fixture
  role: bridge
```

`allowedHosts` adds literal DNS or IPv4 authorities to the built-in loopback
and Service allowlist. Include the exact port when the client sends one.
Forward the local Service on `127.0.0.1`. A client that needs the custom
authority can set the explicit `Host` header while connecting to the forward.
For the bridge, use the numeric loopback `/mcp` URL, the matching `--board`,
and the Job's project-scoped bearer in `PLOTROOM_TOKEN`. Use a separately
issued project bearer for the HTTP snapshot client.

The smoke consumes and removes the Job's private handoff file without
printing it. Keep real-client acceptance separate from the scripted smoke.

## Verify the Switchboard bridge locally

Build a fixture image and pass the smoke helper a fresh Compose project name.
It checks project readiness and an authenticated snapshot, then restarts the
Compose service and verifies that the same bearer still works and a fixture
decision persists. It also uses a scripted relay to verify durable delivery
before acknowledgment, deduplication after a bridge restart, and an outbound
delivery. It removes the fixture Compose volume on exit.

```sh
docker build --tag plotroom:bridge-local .
uv run --locked python deploy/bridge_smoke.py --image-tag bridge-local \
  --project "plotroom-bridge-$(date +%s)-$$"
```

For an explicit local fixture, set `via_bridge = true` and `board = "fixture-board"` in the
server project's `switchboard` table. Set `switchboard_address` for each participating worker.
On the relay machine, make `switchboard-relay` available on PATH or pass a relay command with
`--relay-command`. Configure `PLOTROOM_TOKEN` with that project's bridge bearer and, from the
checkout, run `uv run --locked plotroom bridge --url http://127.0.0.1:8765/mcp --board fixture-board`.
The daemon retains outgoing Switchboard actions until the bridge completes a
lease. The relay receives the same action key on each delivery attempt.
Lane-pinned budget stops require a local lane fence. A bridged project reports
these as failed actions for operator attention.
