"""
Tests for SSE streaming parser and Stream/AsyncStream iterators.
"""

from __future__ import annotations

import json

import pytest

from infrgate_sdk import InfrGateError, StreamChunk
from infrgate_sdk.streaming import AsyncStream, Stream, _parse_sse_line


# ── SSE Parser Tests ──────────────────────────────────────────────────────────


class TestParseSSELine:
    def _make_chunk_json(self, **overrides) -> str:
        chunk = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
        }
        chunk.update(overrides)
        return f"data: {json.dumps(chunk)}"

    def test_valid_data_line(self):
        line = self._make_chunk_json()
        result = _parse_sse_line(line)
        assert isinstance(result, StreamChunk)
        assert result.id == "chatcmpl-test"
        assert result.choices[0].delta.content == "hi"

    def test_done_signal(self):
        result = _parse_sse_line("data: [DONE]")
        assert result is None

    def test_empty_line(self):
        result = _parse_sse_line("")
        assert result is None

    def test_comment_line(self):
        result = _parse_sse_line(": keep-alive")
        assert result is None

    def test_non_data_line(self):
        result = _parse_sse_line("event: ping")
        assert result is None

    def test_malformed_json(self):
        result = _parse_sse_line("data: {invalid json}")
        assert result is None

    def test_error_chunk_raises(self):
        error_data = {"error": {"type": "provider_error", "message": "Upstream failed", "code": 502}}
        line = f"data: {json.dumps(error_data)}"
        with pytest.raises(InfrGateError) as exc_info:
            _parse_sse_line(line)
        assert "Upstream failed" in exc_info.value.message

    def test_chunk_with_usage(self):
        line = self._make_chunk_json(
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            choices=[],
        )
        result = _parse_sse_line(line)
        assert result is not None
        assert result.usage is not None
        assert result.usage.total_tokens == 15

    def test_finish_reason(self):
        line = self._make_chunk_json(
            choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}]
        )
        result = _parse_sse_line(line)
        assert result is not None
        assert result.choices[0].finish_reason == "stop"


# ── Sync Stream Tests ────────────────────────────────────────────────────────


class TestSyncStream:
    def _make_lines(self, contents: list[str]) -> list[str]:
        lines = []
        for content in contents:
            chunk = {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1700000000,
                "model": "test-model",
                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
            }
            lines.append(f"data: {json.dumps(chunk)}")
        lines.append("data: [DONE]")
        return lines

    def test_iterate_chunks(self):
        lines = self._make_lines(["Hello", " ", "world"])
        stream = Stream(iter(lines))
        chunks = list(stream)
        assert len(chunks) == 3
        assert chunks[0].choices[0].delta.content == "Hello"
        assert chunks[2].choices[0].delta.content == "world"

    def test_collect(self):
        lines = self._make_lines(["Hello", " ", "world", "!"])
        stream = Stream(iter(lines))
        result = stream.collect()
        assert result == "Hello world!"

    def test_empty_stream(self):
        stream = Stream(iter(["data: [DONE]"]))
        chunks = list(stream)
        assert chunks == []

    def test_context_manager(self):
        lines = self._make_lines(["A", "B"])
        with Stream(iter(lines)) as stream:
            first = next(stream)
            assert first.choices[0].delta.content == "A"
        # Should not raise even if not fully consumed

    def test_stops_after_done(self):
        """Stream stops yielding after [DONE], even if more lines exist."""
        lines = self._make_lines(["A"])
        lines.append("data: should-not-appear")  # After [DONE]
        stream = Stream(iter(lines))
        chunks = list(stream)
        assert len(chunks) == 1


# ── Async Stream Tests ───────────────────────────────────────────────────────


class TestAsyncStream:
    def _make_lines(self, contents: list[str]) -> list[str]:
        lines = []
        for content in contents:
            chunk = {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1700000000,
                "model": "test-model",
                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
            }
            lines.append(f"data: {json.dumps(chunk)}")
        lines.append("data: [DONE]")
        return lines

    async def _async_iter(self, items):
        for item in items:
            yield item

    @pytest.mark.asyncio
    async def test_async_iterate(self):
        lines = self._make_lines(["Hello", " ", "world"])
        stream = AsyncStream(self._async_iter(lines))
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        assert len(chunks) == 3

    @pytest.mark.asyncio
    async def test_async_collect(self):
        lines = self._make_lines(["Hello", " ", "world"])
        stream = AsyncStream(self._async_iter(lines))
        result = await stream.collect()
        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        lines = self._make_lines(["A", "B"])
        async with AsyncStream(self._async_iter(lines)) as stream:
            chunk = await stream.__anext__()
            assert chunk.choices[0].delta.content == "A"

    @pytest.mark.asyncio
    async def test_async_empty_stream(self):
        stream = AsyncStream(self._async_iter(["data: [DONE]"]))
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        assert chunks == []
