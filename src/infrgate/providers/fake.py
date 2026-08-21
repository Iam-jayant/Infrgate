"""
Fake adapter — injects failures and latency for testing.
"""

import asyncio
from infrgate.exceptions import ProviderError, ProviderTimeoutError
from infrgate.providers.base import ProviderAdapter, ProviderRequest, ProviderResponse


class FakeAdapter(ProviderAdapter):
    """Adapter that returns dummy data and can inject failures."""

    def __init__(self):
        self.should_fail = False
        self.should_timeout = False
        self.latency_s = 0.0

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
