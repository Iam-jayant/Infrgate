"""
Fake adapter — injects failures and latency for testing.
"""

import asyncio
from typing import AsyncIterator

from infrgate.exceptions import ProviderError, ProviderTimeoutError
from infrgate.providers.base import ProviderAdapter, ProviderRequest, ProviderResponse
from infrgate.schemas.streaming import StreamChunk


class FakeAdapter(ProviderAdapter):
    """Adapter that returns dummy data and can inject failures."""

    def __init__(self):
        self.should_fail = False
        self.should_timeout = False
        self.latency_s = 0.0
        self.fail_after_chunks = -1

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def supported_models(self) -> list[str]:
        return ["fake-model"]

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if self.latency_s > 0:
            await asyncio.sleep(self.latency_s)

        if self.should_timeout:
            raise ProviderTimeoutError("fake", 30.0)

        if self.should_fail:
            raise ProviderError("Fake failure", provider="fake", retryable=True, upstream_status=500)

        return ProviderResponse(
            content="Fake response content",
            model=request.model,
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            provider_latency_ms=int(self.latency_s * 1000),
            raw_response={"dummy": True}
        )

    async def stream(self, request: ProviderRequest) -> AsyncIterator[StreamChunk]:
        if self.latency_s > 0:
            await asyncio.sleep(self.latency_s)

        if self.should_timeout:
            raise ProviderTimeoutError("fake", 30.0)

        if self.should_fail and self.fail_after_chunks <= 0:
            raise ProviderError("Fake failure before first chunk", provider="fake", retryable=True, upstream_status=500)

        chunks_to_send = self.fail_after_chunks if self.fail_after_chunks > 0 else 3

        for i in range(chunks_to_send):
            if self.latency_s > 0:
                await asyncio.sleep(self.latency_s / chunks_to_send)

            if i == 0:
                yield StreamChunk(
                    id=request.request_id,
                    model=request.model,
                    delta_role="assistant"
                )

            yield StreamChunk(
                id=request.request_id,
                model=request.model,
                delta_content=f" chunk {i}"
            )

        if self.should_fail and self.fail_after_chunks > 0:
            raise ProviderError("Fake failure mid-stream", provider="fake", retryable=True, upstream_status=500)

        yield StreamChunk(
            id=request.request_id,
            model=request.model,
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        )
