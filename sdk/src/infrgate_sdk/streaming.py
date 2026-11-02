"""
Streaming iterators for SSE-based chat completions.

Wraps raw SSE lines from the transport into typed `StreamChunk` objects,
handling the OpenAI SSE protocol: `data: {...}` lines and `data: [DONE]`.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Iterator

from infrgate_sdk.exceptions import InfrGateError
from infrgate_sdk.models import StreamChunk


def _parse_sse_line(line: str) -> StreamChunk | None:
    """
    Parse a single SSE data line into a StreamChunk.

    Returns None for non-data lines, [DONE] signals, or empty chunks.
    """
    if not line.startswith("data: "):
        return None

    payload = line[6:].strip()

    if payload == "[DONE]":
        return None

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    # Check for inline error responses from the gateway
    if "error" in data and "choices" not in data:
        error = data["error"]
        raise InfrGateError(
            message=error.get("message", "Stream error"),
            status_code=error.get("code"),
            error_type=error.get("type", "stream_error"),
        )

    return StreamChunk.model_validate(data)


class Stream:
    """
    Synchronous streaming iterator over chat completion chunks.

    Usage::

        stream = client.chat.completions.create(
            model="...", messages=[...], stream=True
        )
        for chunk in stream:
            print(chunk.choices[0].delta.content, end="")
    """

    def __init__(self, raw_lines: Iterator[str]):
        self._raw_lines = raw_lines
        self._exhausted = False

    def __iter__(self) -> Iterator[StreamChunk]:
        return self

    def __next__(self) -> StreamChunk:
        if self._exhausted:
            raise StopIteration

        for line in self._raw_lines:
            chunk = _parse_sse_line(line)
            if chunk is not None:
                return chunk

        self._exhausted = True
        raise StopIteration

    def __enter__(self):
        return self

    def __exit__(self, *args):
        # Exhaust remaining lines to release the connection
        if not self._exhausted:
            for _ in self._raw_lines:
                pass
            self._exhausted = True

    def collect(self) -> str:
        """
        Consume the entire stream and return the concatenated content.

        Convenience method for when you want streaming transport but
        don't need to process chunks incrementally.
        """
        parts: list[str] = []
        for chunk in self:
            if chunk.choices and chunk.choices[0].delta.content:
                parts.append(chunk.choices[0].delta.content)
        return "".join(parts)


class AsyncStream:
    """
    Asynchronous streaming iterator over chat completion chunks.

    Usage::

        stream = await client.chat.completions.create(
            model="...", messages=[...], stream=True
        )
        async for chunk in stream:
            print(chunk.choices[0].delta.content, end="")
    """

    def __init__(self, raw_lines: AsyncIterator[str]):
        self._raw_lines = raw_lines
        self._exhausted = False

    def __aiter__(self) -> AsyncIterator[StreamChunk]:
        return self

    async def __anext__(self) -> StreamChunk:
        if self._exhausted:
            raise StopAsyncIteration

        async for line in self._raw_lines:
            chunk = _parse_sse_line(line)
            if chunk is not None:
                return chunk

        self._exhausted = True
        raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if not self._exhausted:
            async for _ in self._raw_lines:
                pass
            self._exhausted = True

    async def collect(self) -> str:
        """Consume the entire stream and return concatenated content."""
        parts: list[str] = []
        async for chunk in self:
            if chunk.choices and chunk.choices[0].delta.content:
                parts.append(chunk.choices[0].delta.content)
        return "".join(parts)
