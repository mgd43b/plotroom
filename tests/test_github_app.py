"""Exercise GitHub App credentials against a disposable loopback provider."""

import asyncio
import base64
import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from conftest import write_config
from fakes import FixedClock
from plotroom.adapters.github import GitHubRateLimited, GitHubUnavailable
from plotroom.adapters.github.app_credential import GitHubAppCredential
from plotroom.app.config import ConfigError, load_config
from plotroom.app.serve import Daemon
from plotroom.core.events import format_time
from test_github import pull

REPO = "fixture/example"
CLIENT = "Iv1.fixture"
INSTALLATION = 7
PERMISSIONS = {
    "actions": "read",
    "checks": "read",
    "contents": "read",
    "deployments": "read",
    "issues": "read",
    "pull_requests": "read",
}
MAIN = "b" * 40


def installation_token(index: int) -> str:
    return f"ghs_synthetic_fixture_credential_{index}"


def decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


@dataclass
class AppFixture:
    clock: FixedClock
    key_file: Path
    url: str = ""
    mode: str = ""
    requests: list[str] = field(default_factory=lambda: [])
    issued: int = 0
    pull_generations: list[int] = field(default_factory=lambda: [])
    jwts: set[str] = field(default_factory=lambda: set(), repr=False)

    def credential(self) -> GitHubAppCredential:
        return GitHubAppCredential(CLIENT, INSTALLATION, self.key_file, REPO, self.url, self.clock)


@pytest.fixture
def app_fixture(tmp_path: Path) -> Iterator[AppFixture]:
    # This key exists only in this test directory and has no corresponding GitHub App.
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_file = tmp_path / "fixture-key.pem"
    key_file.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    public = private.public_key()
    data = AppFixture(FixedClock(), key_file)

    class Handler(BaseHTTPRequestHandler):
        def send_json(self, code: int, body: Any) -> None:
            encoded = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def verify_jwt(self) -> None:
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise AssertionError("App request is missing its JWT bearer")
            token = authorization.removeprefix("Bearer ")
            header, claims, sig = token.split(".")
            expected = int(data.clock.now().timestamp())
            assert json.loads(decode(header)) == {"typ": "JWT", "alg": "RS256"}
            assert json.loads(decode(claims)) == {
                "iss": CLIENT,
                "iat": expected - 60,
                "exp": expected + 540,
            }
            public.verify(
                decode(sig), f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
            )
            data.jwts.add(token)
            assert self.headers["X-GitHub-Api-Version"] == "2026-03-10"

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            data.requests.append(path)
            if path == f"/app/installations/{INSTALLATION}":
                self.verify_jwt()
                if data.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", f"{data.url}/must-not-follow")
                    self.end_headers()
                    return
                owner = "other" if data.mode == "owner" else "Fixture"
                self.send_json(200, {"account": {"login": owner}})
                return
            if path == "/must-not-follow":
                self.send_json(500, {})
                return
            if self.headers.get("Authorization") != f"Bearer {installation_token(data.issued)}":
                self.send_json(401, {"message": "fixture bearer absent"})
                return
            if path == f"/repos/{REPO}/pulls":
                data.pull_generations.append(data.issued)
                self.send_json(200, [pull(seconds=data.issued, head_repository=REPO)])
                return
            if path == f"/repos/{REPO}":
                self.send_json(200, {"default_branch": "main"})
            elif path.endswith("/branches/main"):
                self.send_json(200, {"commit": {"sha": MAIN}})
            elif path.endswith("/actions/runs"):
                self.send_json(200, {"workflow_runs": []})
            elif path.endswith("/check-runs"):
                self.send_json(200, {"check_runs": [], "total_count": 0})
            else:
                self.send_json(200, [])

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            data.requests.append(path)
            if path == "/graphql":
                self.send_json(404, {})
                return
            assert path == f"/app/installations/{INSTALLATION}/access_tokens"
            self.verify_jwt()
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body == {"repositories": ["example"], "permissions": PERMISSIONS}
            if data.mode == "denied":
                self.send_json(403, {"message": installation_token(1)})
                return
            if data.mode == "limit":
                self.send_json(429, {})
                return
            expires = data.clock.now() + timedelta(seconds=6)
            if data.mode == "expired":
                expires = data.clock.now()
            value = "invalid token" if data.mode == "token" else installation_token(data.issued + 1)
            if not data.mode:
                data.issued += 1
            stamp = (
                expires.replace(tzinfo=None).isoformat()
                if data.mode == "naive"
                else expires.isoformat()
            )
            self.send_json(201, {"token": value, "expires_at": stamp})

        def log_message(self, format: str, *args: Any) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        data.url = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield data
        finally:
            server.shutdown()
            thread.join()


async def test_app_daemon_keeps_polling_across_short_installation_tokens(
    tmp_path: Path,
    app_fixture: AppFixture,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = write_config(tmp_path)
    path.write_text(
        path.read_text() + f'\n[projects.demo.github]\nrepository = "{REPO}"\n'
        f'api_url = "{app_fixture.url}"\npoll_seconds = 1\n'
        f'[projects.demo.github.app]\nclient_id = "{CLIENT}"\n'
        f'installation_id = {INSTALLATION}\nprivate_key_file = "fixture-key.pem"\n'
    )
    monkeypatch.setenv("PLOTROOM_DEMO_GITHUB_TOKEN", "different-local-fixture")
    config = load_config(path, {})
    caplog.set_level(logging.INFO)
    daemon = Daemon(config, clock=app_fixture.clock)
    serving = asyncio.create_task(daemon.run())
    try:
        async with asyncio.timeout(15):
            await daemon.wait_started()
            ledger = daemon.ledgers["demo"]

            async def observed(generation: int) -> None:
                expected = format_time(
                    datetime.fromisoformat(pull(seconds=generation)["updated_at"])
                )
                while True:
                    projected = await ledger.pull_request(REPO, 12)
                    if (
                        projected is not None
                        and projected.updated_at == expected
                        and ledger.github_error is None
                    ):
                        break
                    await asyncio.sleep(0.05)
                assert generation in app_fixture.pull_generations
                assert ledger.github_error is None
                _, github = await ledger.snapshot_board("orchestrator")
                assert github is not None
                assert github.open_prs == 1

            await observed(1)
            app_fixture.clock.advance(5)
            await observed(2)
            assert app_fixture.issued == 2
            app_fixture.mode = "denied"
            app_fixture.clock.advance(5)
            while ledger.github_error != "Cannot mint a GitHub App installation token":  # noqa: ASYNC110
                await asyncio.sleep(0.05)
            assert app_fixture.issued == 2
            app_fixture.mode = ""
            await observed(3)
    finally:
        daemon.stop()
        await serving

    mount = app_fixture.key_file.read_bytes().splitlines()[1:-1]
    secrets = [
        *mount,
        *(installation_token(n).encode() for n in range(1, 4)),
        *(jwt.encode() for jwt in app_fixture.jwts),
    ]
    evidence = [path.read_bytes(), caplog.text.encode(), repr(config).encode()]
    evidence.extend(p.read_bytes() for p in config.data_dir.rglob("*") if p.is_file())
    if any(secret in artifact for secret in secrets for artifact in evidence):
        raise AssertionError("fixture credential leaked into recorded project evidence")


@pytest.mark.parametrize(
    ("mode", "message", "mint"),
    [
        ("owner", "does not own", False),
        ("redirect", "Cannot read the configured", False),
        ("expired", "expired or invalid", True),
        ("naive", "expired or invalid", True),
        ("token", "invalid installation token", True),
        ("denied", "Cannot mint", True),
    ],
)
async def test_app_refuses_unusable_provider_evidence(
    app_fixture: AppFixture, mode: str, message: str, mint: bool
) -> None:
    app_fixture.mode = mode
    with pytest.raises(GitHubUnavailable, match=message):
        await app_fixture.credential().token()
    assert (f"/app/installations/{INSTALLATION}/access_tokens" in app_fixture.requests) == mint
    assert "/must-not-follow" not in app_fixture.requests


async def test_app_serializes_refresh_and_handles_rate_limits(app_fixture: AppFixture) -> None:
    credential = app_fixture.credential()
    await asyncio.gather(*(credential.token() for _ in range(8)))
    assert app_fixture.issued == 1
    app_fixture.clock.advance(5)
    app_fixture.mode = "limit"
    with pytest.raises(GitHubRateLimited):
        await credential.token()
    assert app_fixture.issued == 1
    app_fixture.mode = ""
    await asyncio.gather(*(credential.token() for _ in range(8)))
    assert app_fixture.issued == 2


async def test_app_malformed_key_fails_before_http(app_fixture: AppFixture) -> None:
    app_fixture.key_file.write_text("invalid fixture PEM", encoding="utf-8")
    with pytest.raises(GitHubUnavailable, match="Cannot read or sign"):
        await app_fixture.credential().token()
    assert app_fixture.requests == []


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ('token_file="pat"\n', "cannot be combined"),
        ('api_url="https://api.github.com.invalid"\n', "loopback fixture"),
    ],
)
def test_app_defaults_to_verified_origin_and_refuses_mixed_settings(
    tmp_path: Path, settings: str, message: str
) -> None:
    path = write_config(tmp_path)
    path.write_text(
        path.read_text()
        + f'\n[projects.demo.github]\nrepository = "{REPO}"\n'
        + settings
        + f'[projects.demo.github.app]\nclient_id = "{CLIENT}"\n'
        + f'installation_id = {INSTALLATION}\nprivate_key_file = "fixture-key.pem"\n'
    )
    with pytest.raises(ConfigError, match=message):
        load_config(path, {})


async def test_app_invalidates_cached_token_on_clock_regression(app_fixture: AppFixture) -> None:
    credential = app_fixture.credential()
    await credential.token()
    app_fixture.clock.advance(2)
    await credential.token()
    assert app_fixture.issued == 1
    app_fixture.clock.advance(-1)
    with pytest.raises(GitHubUnavailable, match="clock moved backward"):
        await credential.token()
    assert app_fixture.issued == 1
    app_fixture.clock.advance(1)
    await credential.token()
    assert app_fixture.issued == 2
