"""Attribute and read an Actions job's reserved JUnit artifact."""

import asyncio
import hashlib
import re
from ipaddress import ip_address

import httpx

from plotroom.adapters.github import GitHubUnavailable
from plotroom.adapters.github.junit_xml import junit_evidence
from plotroom.adapters.github.read import GitHubReadClient, Row, number, obj, string

MAX_ATTEMPTS = 5
MAX_JOBS = 300
MAX_ARCHIVE = 1024 * 1024
DOWNLOAD_TIMEOUT = 15.0


class JUnitReader:
    """Reuse checked run and attempt metadata within one check-list refresh."""

    def __init__(self, api: GitHubReadClient, client: httpx.AsyncClient, token: str) -> None:
        self.api = api
        self.client = client
        self.token = token
        self.runs: dict[int, Row] = {}
        self.jobs: dict[tuple[int, int], list[Row]] = {}

    async def _run(self, suite: int) -> Row:
        if suite not in self.runs:
            runs, more = await self.api.page(
                self.client,
                self.token,
                "/actions/runs",
                1,
                key="workflow_runs",
                params={"check_suite_id": suite},
                verify_total=True,
            )
            if more or len(runs) != 1 or number(runs[0]["check_suite_id"]) != suite:
                raise ValueError("GitHub could not attribute the check suite to one Actions run")
            self.runs[suite] = runs[0]
        return self.runs[suite]

    async def _jobs(self, run: int, attempt: int) -> list[Row]:
        key = (run, attempt)
        if key not in self.jobs:
            jobs: list[Row] = []
            async for job in self.api.pages(
                self.client,
                self.token,
                f"/actions/runs/{run}/attempts/{attempt}/jobs",
                key="jobs",
                verify_total=True,
                verify_boundaries=True,
            ):
                jobs.append(job)
                if len(jobs) > MAX_JOBS:
                    raise ValueError("Actions attempt exceeded the JUnit job limit")
            if len({number(job["id"]) for job in jobs}) != len(jobs):
                raise ValueError("GitHub returned duplicate Actions jobs")
            self.jobs[key] = jobs
        return self.jobs[key]

    def _check_id(self, job: Row) -> int:
        prefix = f"{self.api.endpoint}/check-runs/".lower()
        url = string(job["check_run_url"]).lower()
        check_id = url.removeprefix(prefix)
        if url == check_id or not re.fullmatch(r"[1-9][0-9]{0,18}", check_id):
            raise ValueError("GitHub returned an invalid Actions job check link")
        return number(int(check_id))

    async def _job(self, run: int, attempts: int, check: Row) -> tuple[int, str]:
        matches: list[tuple[int, str]] = []
        for attempt in range(1, attempts + 1):
            jobs = await self._jobs(run, attempt)
            names = [string(job["name"]) for job in jobs]
            for job, name in zip(jobs, names, strict=True):
                if self._check_id(job) != check["id"]:
                    continue
                if (
                    not name
                    or names.count(name) != 1
                    or number(job["run_id"]) != run
                    or job["head_sha"] != check["head_sha"]
                    or number(job.get("run_attempt", attempt)) != attempt
                    or job["status"] != "completed"
                    or job["conclusion"] != check["conclusion"]
                ):
                    raise ValueError("GitHub returned inconsistent Actions job evidence")
                matches.append((attempt, name))
        if len(matches) != 1:
            raise ValueError("GitHub could not attribute the check to one Actions attempt job")
        return matches[0]

    async def evidence(self, check: Row, suite: int, *, passed: bool) -> list[Row] | None:
        """Return None for verified absence or ineligibility."""
        run = await self._run(suite)
        repository = obj(run["repository"])
        head_repository = obj(run["head_repository"])
        repository_id = number(repository["id"])
        head_id = number(head_repository["id"])
        project_name = self.api.repository.lower()
        head_name = string(head_repository["full_name"]).lower()
        run_id, attempts = number(run["id"]), number(run["run_attempt"])
        sha = check["head_sha"]
        if (
            string(repository["full_name"]).lower() != project_name
            or run["head_sha"] != sha
            or not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", head_name)
            or (head_name == project_name) != (head_id == repository_id)
        ):
            raise ValueError("Actions run does not match the repository and check head")
        if head_id != repository_id:
            # The run is positively identified as using a foreign head.
            return None
        if attempts > MAX_ATTEMPTS:
            raise ValueError("Actions run exceeded the JUnit attempt limit")
        attempt, job_name = await self._job(run_id, attempts, check)
        name = f"plotroom-junit-{run_id}-{attempt}-{hashlib.sha256(job_name.encode()).hexdigest()}"
        artifacts, more = await self.api.page(
            self.client,
            self.token,
            f"/actions/runs/{run_id}/artifacts",
            1,
            key="artifacts",
            params={"name": name},
            verify_total=True,
        )
        if more or len(artifacts) > 1:
            raise ValueError("GitHub returned ambiguous JUnit artifacts")
        if not artifacts:
            return None
        artifact = artifacts[0]
        context = obj(artifact["workflow_run"])
        size = number(artifact["size_in_bytes"])
        digest = string(artifact["digest"])
        if (
            artifact["name"] != name
            or artifact["expired"] is not False
            or size > MAX_ARCHIVE
            or number(context["id"]) != run_id
            or number(context["repository_id"]) != repository_id
            or number(context["head_repository_id"]) != repository_id
            or context["head_sha"] != sha
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        ):
            raise ValueError("GitHub returned inconsistent JUnit artifact metadata")
        location = await self.api.artifact_location(self.client, self.token, number(artifact["id"]))
        archive = await _download(_download_url(location, self.api.api_url), size)
        if f"sha256:{hashlib.sha256(archive).hexdigest()}" != digest:
            raise ValueError("JUnit artifact digest did not match")
        return junit_evidence(archive, passed=passed)


def _loopback_origin(url: httpx.URL, api: httpx.URL) -> bool:
    if (url.scheme, url.host, url.port) != (api.scheme, api.host, api.port):
        return False
    try:
        return ip_address(api.host).is_loopback
    except ValueError:
        return False


def _download_url(value: str, api_url: str) -> httpx.URL:
    if any(ord(character) <= 32 or ord(character) >= 127 for character in value) or any(
        character in value for character in ("\\", "#")
    ):
        raise ValueError("Invalid JUnit download location")
    url = httpx.URL(value)
    if url.userinfo or url.scheme not in {"https", "http"}:
        raise ValueError("Invalid JUnit download origin")
    if _loopback_origin(url, httpx.URL(api_url)):
        return url
    azure_blob = re.fullmatch(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net", url.host)
    if (
        url.scheme == "https"
        and url.port is None
        and (url.host == "results-receiver.actions.githubusercontent.com" or azure_blob)
    ):
        return url
    raise ValueError("Untrusted JUnit download origin")


async def _download(url: httpx.URL, size: int) -> bytes:
    # A fresh transport has no credentials, cookies, proxies or redirects. Sending
    # directly also keeps the signed location out of the HTTPX request logger.
    request = httpx.Request("GET", url, headers={"Accept-Encoding": "identity"})
    async with (
        asyncio.timeout(DOWNLOAD_TIMEOUT),
        httpx.AsyncHTTPTransport(trust_env=False) as transport,
    ):
        response = await transport.handle_async_request(request)
        try:
            if (
                response.status_code != 200
                or response.headers.get("Content-Encoding", "identity").lower() != "identity"
            ):
                raise GitHubUnavailable("JUnit artifact download was unavailable")
            body = bytearray()
            async for chunk in response.aiter_raw():
                if len(body) + len(chunk) > size:
                    raise GitHubUnavailable("JUnit artifact download exceeded its declared size")
                body.extend(chunk)
            if len(body) != size:
                raise GitHubUnavailable("JUnit artifact download was incomplete")
            return bytes(body)
        finally:
            await response.aclose()
