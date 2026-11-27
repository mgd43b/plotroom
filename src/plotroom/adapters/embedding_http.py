"""HTTP adapter for endpoints accepting the embeddings request and response format."""

import asyncio
import json
from http import HTTPStatus
from pathlib import Path
from typing import Any, Never, cast

import httpx

from plotroom._secret_file import read_secret_file
from plotroom.ports.search import (
    EmbeddingError,
    EmbeddingModel,
    EmbeddingOutcome,
    EmbeddingRejection,
)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REJECTION_BYTES = 4 * 1024


class _BatchTooLarge(EmbeddingError):
    """The request or its response exceeded a size limit."""


class HttpEmbedder:
    """POST model, input and dimensions, then restore the response's input order."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        model: EmbeddingModel,
        *,
        key_file: Path | None = None,
    ) -> None:
        self._client = client
        self._url = url
        self._model = model
        self._key_file = key_file

    async def embed(self, texts: tuple[str, ...]) -> tuple[EmbeddingOutcome, ...]:
        try:
            return await self._embed_batch(texts)
        except _BatchTooLarge:
            if len(texts) <= 1:
                raise
        # Discard all outcomes if either half raises a batch-level failure.
        middle = len(texts) // 2
        first = await self.embed(texts[:middle])
        return first + await self.embed(texts[middle:])

    async def _embed_batch(self, texts: tuple[str, ...]) -> tuple[EmbeddingOutcome, ...]:
        content = bytearray()
        try:
            async with asyncio.timeout(10):
                headers = {"Accept-Encoding": "identity"}
                if self._key_file is not None:
                    endpoint = httpx.URL(self._url)
                    if endpoint.scheme != "https" or not endpoint.host or endpoint.userinfo:
                        raise EmbeddingError("embedding key file requires an HTTPS endpoint")
                    key = await self._read_key(self._key_file)
                    headers["Authorization"] = f"Bearer {key}"
                async with self._client.stream(
                    "POST",
                    self._url,
                    headers=headers,
                    follow_redirects=(
                        False if self._key_file is not None else httpx.USE_CLIENT_DEFAULT
                    ),
                    json={
                        "model": self._model.name,
                        "input": texts,
                        "dimensions": self._model.dimension,
                    },
                ) as response:
                    if (
                        response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                        and "Retry-After" not in response.headers
                    ):
                        if len(texts) != 1:
                            raise _BatchTooLarge("embedding endpoint request exceeded size limit")
                        if await _confirmed_input_rejection(response):
                            return (EmbeddingRejection.INPUT_TOO_LARGE,)
                        raise EmbeddingError("embedding endpoint returned an unconfirmed rejection")
                    response.raise_for_status()
                    # Refuse compression so one decoder call cannot inflate past the cap.
                    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise EmbeddingError("embedding endpoint returned unsupported encoding")
                    async for chunk in response.aiter_raw(chunk_size=64 * 1024):
                        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise _BatchTooLarge("embedding endpoint response exceeded size limit")
                        content.extend(chunk)
        except httpx.HTTPError, httpx.InvalidURL, TimeoutError:
            raise EmbeddingError("embedding endpoint request failed") from None
        return await asyncio.to_thread(self._decode_batch, content, len(texts))

    @staticmethod
    async def _read_key(path: Path) -> str:
        try:
            key = await read_secret_file(path)
        except OSError, ValueError:
            raise EmbeddingError("embedding key file could not be read") from None
        if not key or not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
            raise EmbeddingError("embedding key file is invalid")
        return key

    def _decode_batch(self, content: bytearray, size: int) -> tuple[tuple[float, ...], ...]:
        try:
            payload: Any = json.loads(content)
        except (ValueError, RecursionError) as exc:
            raise EmbeddingError("embedding endpoint returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise EmbeddingError("embedding endpoint returned an invalid response")
        data = cast("dict[str, Any]", payload).get("data")
        if not isinstance(data, list) or len(cast("list[Any]", data)) != size:
            raise EmbeddingError("embedding endpoint returned an invalid vector count")

        vectors: dict[int, tuple[float, ...]] = {}
        for record in cast("list[Any]", data):
            if not isinstance(record, dict):
                raise EmbeddingError("embedding endpoint returned an invalid record")
            item = cast("dict[str, Any]", record)
            index, vector = item.get("index"), item.get("embedding")
            if type(index) is not int or not 0 <= index < size or index in vectors:
                raise EmbeddingError("embedding endpoint returned an invalid input index")
            if not isinstance(vector, list):
                raise EmbeddingError("embedding endpoint returned an invalid vector")
            numbers = cast("list[Any]", vector)
            if len(numbers) != self._model.dimension or any(
                type(number) not in (float, int) for number in numbers
            ):
                raise EmbeddingError("embedding endpoint returned an invalid vector")
            try:
                vectors[index] = tuple(float(number) for number in numbers)
            except OverflowError as exc:
                raise EmbeddingError("embedding endpoint returned an out-of-range value") from exc
        return tuple(vectors[index] for index in range(size))


async def _confirmed_input_rejection(response: httpx.Response) -> bool:
    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
        return False
    body = bytearray()
    async for chunk in response.aiter_raw(chunk_size=MAX_REJECTION_BYTES + 1):
        if len(body) + len(chunk) > MAX_REJECTION_BYTES:
            return False
        body.extend(chunk)
    try:
        payload: Any = json.loads(
            body.decode("utf-8"),
            parse_constant=_invalid_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except ValueError, RecursionError:
        return False
    if not isinstance(payload, dict):
        return False
    error = cast("dict[str, Any]", payload).get("error")
    return (
        isinstance(error, dict)
        and cast("dict[str, Any]", error).get("code") == EmbeddingRejection.INPUT_TOO_LARGE
    )


def _invalid_json_constant(_value: str) -> Never:
    raise ValueError("nonstandard JSON constant")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON object key")
    return result
