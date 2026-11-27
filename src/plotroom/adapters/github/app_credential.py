"""Mint repository-scoped GitHub App installation tokens from a mounted key."""

import asyncio
import base64
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from plotroom._secret_file import read_secret_file
from plotroom.adapters.github import (
    GITHUB_API,
    GitHubRateLimited,
    GitHubUnavailable,
    is_rate_limited,
)
from plotroom.ports.clock import Clock

_READ_PERMISSIONS = {
    "actions": "read",
    "checks": "read",
    "contents": "read",
    "deployments": "read",
    "issues": "read",
    "pull_requests": "read",
}
_BEARER = re.compile(r"[A-Za-z0-9._~-]+")
_MAX_RESPONSE_BYTES = 64 * 1024


def _base64url(value: bytes) -> bytes:
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def _app_jwt(pem: str, client_id: str, now: datetime) -> str:
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("an RSA key is required")
    timestamp = int(now.timestamp())
    claims = {"iss": client_id, "iat": timestamp - 60, "exp": timestamp + 540}
    unsigned = b".".join(
        (
            _base64url(b'{"alg":"RS256","typ":"JWT"}'),
            _base64url(json.dumps(claims, separators=(",", ":")).encode("utf-8")),
        )
    )
    signature = key.sign(unsigned, padding.PKCS1v15(), hashes.SHA256())
    return b".".join((unsigned, _base64url(signature))).decode("ascii")


@dataclass(frozen=True)
class _InstallationToken:
    value: str = field(repr=False)
    refresh_at: datetime


async def _response_payload(
    response: httpx.Response, expected_status: int, error: str
) -> dict[str, Any]:
    if response.status_code == 429 or (
        response.status_code == 403
        and (
            response.headers.get("X-RateLimit-Remaining") == "0"
            or "Retry-After" in response.headers
        )
    ):
        raise GitHubRateLimited(response.headers)
    if response.status_code not in (expected_status, 403):
        raise GitHubUnavailable(error)
    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
        # Avoid decompressing a large response before enforcing the byte limit.
        raise GitHubUnavailable(error)

    body = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=4096):
        if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
            raise GitHubUnavailable(error)
        body.extend(chunk)
    if response.status_code == 403:
        if is_rate_limited(httpx.Response(403, content=bytes(body))):
            raise GitHubRateLimited(response.headers)
        raise GitHubUnavailable(error)

    try:
        payload: Any = json.loads(body)
    except ValueError, RecursionError:
        raise GitHubUnavailable(error) from None
    if not isinstance(payload, dict):
        raise GitHubUnavailable(error)
    return cast("dict[str, Any]", payload)


class GitHubAppCredential:
    def __init__(
        self,
        client_id: str,
        installation_id: int,
        key_file: Path,
        repository: str,
        api_url: str,
        clock: Clock,
    ) -> None:
        if api_url != GITHUB_API:
            raise ValueError("GitHub App credentials require https://api.github.com")
        if type(installation_id) is not int or installation_id < 1:
            raise ValueError("GitHub App credentials require a positive installation ID")
        self._client_id = client_id
        self._key_file = key_file
        self._owner, self._repository = repository.split("/", 1)
        self._installation = f"{GITHUB_API}/app/installations/{installation_id}"
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last_now: datetime | None = None
        self._cached: _InstallationToken | None = None

    def _now(self) -> datetime:
        current = self._clock.now()
        if self._last_now is not None and current < self._last_now:
            self._cached = None
            raise GitHubUnavailable("GitHub App credential clock moved backward")
        self._last_now = current
        return current

    async def token(self) -> str:
        async with self._lock:
            now = self._now()
            if self._cached is not None and now < self._cached.refresh_at:
                return self._cached.value
            self._cached = None
            self._cached = await self._refresh()
            return self._cached.value

    async def reject(self, token: str) -> None:
        async with self._lock:
            if self._cached is not None and self._cached.value == token:
                self._cached = None

    async def _refresh(self) -> _InstallationToken:
        try:
            pem = await read_secret_file(self._key_file)
            jwt = await asyncio.to_thread(_app_jwt, pem, self._client_id, self._now())
        except OSError, ValueError, TypeError, UnsupportedAlgorithm:
            raise GitHubUnavailable(
                "Cannot read or sign with the configured GitHub App key"
            ) from None

        headers = {
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "Accept-Encoding": "identity",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        try:
            async with httpx.AsyncClient(
                timeout=15, follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream("GET", self._installation, headers=headers) as response:
                    installation = await _response_payload(
                        response, 200, "Cannot read the configured GitHub App installation"
                    )
                account: Any = installation.get("account")
                login = (
                    cast("dict[str, Any]", account).get("login")
                    if isinstance(account, dict)
                    else None
                )
                if not isinstance(login, str) or login.casefold() != self._owner.casefold():
                    raise GitHubUnavailable(
                        "GitHub App installation does not own the configured repository"
                    )
                async with client.stream(
                    "POST",
                    f"{self._installation}/access_tokens",
                    headers=headers,
                    json={"repositories": [self._repository], "permissions": _READ_PERMISSIONS},
                ) as response:
                    payload = await _response_payload(
                        response, 201, "Cannot mint a GitHub App installation token"
                    )
        except httpx.RequestError:
            raise GitHubUnavailable("GitHub App credential request failed") from None

        value, expiry = payload.get("token"), payload.get("expires_at")
        if (
            not isinstance(value, str)
            or not _BEARER.fullmatch(value)
            or not isinstance(expiry, str)
        ):
            raise GitHubUnavailable("GitHub returned an invalid installation token")
        try:
            expires_at = datetime.fromisoformat(expiry)
        except ValueError:
            raise GitHubUnavailable(
                "GitHub returned an invalid installation token expiry"
            ) from None
        now = self._now()
        if expires_at.utcoffset() is None or expires_at <= now:
            raise GitHubUnavailable("GitHub returned an expired or invalid installation token")
        # Scale the refresh margin down for short-lived tokens and preserve the fixed
        # upper bound for GitHub's usual one-hour lifetime.
        leeway = min(timedelta(minutes=1), (expires_at - now) / 5)
        return _InstallationToken(value, expires_at - leeway)
