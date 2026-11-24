"""HTTP adapter for endpoints accepting the embeddings request and response format."""

import asyncio
import json
from http import HTTPStatus
from typing import Any, cast

import httpx

from plotroom.ports.search import (
    EmbeddingError,
    EmbeddingModel,
    EmbeddingOutcome,
    EmbeddingRejection,
)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class _BatchTooLarge(EmbeddingError):
    """The request or its response exceeded a size limit."""


class _InputTooLarge(_BatchTooLarge):
    """The provider rejected the request's payload size without a retry hint."""


class HttpEmbedder:
    """POST model, input and dimensions, then restore the response's input order."""

    def __init__(self, client: httpx.AsyncClient, url: str, model: EmbeddingModel) -> None:
        self._client = client
        self._url = url
        self._model = model

    async def embed(self, texts: tuple[str, ...]) -> tuple[EmbeddingOutcome, ...]:
        try:
            return await self._embed_batch(texts)
        except _InputTooLarge:
            if len(texts) == 1:
                return (EmbeddingRejection.INPUT_TOO_LARGE,)
            if not texts:
                raise
        except _BatchTooLarge:
            if len(texts) <= 1:
                raise
        # Discard all outcomes if either half raises a batch-level failure.
        middle = len(texts) // 2
        first = await self.embed(texts[:middle])
        return first + await self.embed(texts[middle:])

    async def _embed_batch(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        content = bytearray()
        try:
            async with asyncio.timeout(10):
                async with self._client.stream(
                    "POST",
                    self._url,
                    headers={"Accept-Encoding": "identity"},
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
                        raise _InputTooLarge("embedding endpoint request exceeded size limit")
                    response.raise_for_status()
                    # Refuse compression so one decoder call cannot inflate past the cap.
                    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise EmbeddingError("embedding endpoint returned unsupported encoding")
                    async for chunk in response.aiter_raw(chunk_size=64 * 1024):
                        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise _BatchTooLarge("embedding endpoint response exceeded size limit")
                        content.extend(chunk)
        except (httpx.HTTPError, httpx.InvalidURL, TimeoutError) as exc:
            raise EmbeddingError("embedding endpoint request failed") from exc
        return await asyncio.to_thread(self._decode_batch, content, len(texts))

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
