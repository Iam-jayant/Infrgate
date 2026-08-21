"""
OpenAI adapter — translates OpenAI-compatible requests to OpenAI API.
"""

from __future__ import annotations

import time
import httpx
import structlog

from infrgate.exceptions import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from infrgate.providers.base import ProviderAdapter, ProviderRequest, ProviderResponse

logger = structlog.get_logger()


class OpenAIAdapter(ProviderAdapter):
    """Provider adapter for OpenAI."""

    BASE_URL = "https://api.openai.com/v1"

    SUPPORTED_MODELS = [
        "gpt-4o",
        "gpt-4o-mini",
    ]

    def __init__(self, api_key: str, http_client: httpx.AsyncClient):
        self._api_key = api_key
        self._client = http_client

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def supported_models(self) -> list[str]:
        return self.SUPPORTED_MODELS

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        url = f"{self.BASE_URL}/chat/completions"
        body = self._translate_request(request)

        logger.info(
            "provider_call",
            provider="openai",
            model=request.model,
            request_id=request.request_id,
        )

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        start = time.monotonic()
        try:
            resp = await self._client.post(
                url,
                json=body,
                headers=headers,
                timeout=30.0,
            )
        except httpx.TimeoutException:
            raise ProviderTimeoutError("openai", 30.0)
        except httpx.ConnectError as e:
            raise ProviderError(
                message=f"Failed to connect to OpenAI: {e}",
                provider="openai",
                retryable=True,
            )

        latency_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            self._handle_error(resp, request.request_id)

        data = resp.json()
        response = self._translate_response(data, request.model, latency_ms)

        logger.info(
            "provider_response",
            provider="openai",
            model=request.model,
            latency_ms=latency_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            request_id=request.request_id,
        )

        return response

    def _translate_request(self, request: ProviderRequest) -> dict:
        body = {
            "model": request.model,
            "messages": request.messages,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.stop:
            body["stop"] = request.stop
        return body

    def _translate_response(
        self,
        data: dict,
        model: str,
        latency_ms: int,
    ) -> ProviderResponse:
        choices = data.get("choices", [])
        if not choices:
            raise ProviderError(
                message="OpenAI returned no choices.",
                provider="openai",
                retryable=False,
                raw_response=data,
            )

        choice = choices[0]
        content = choice.get("message", {}).get("content", "")
        finish_reason = choice.get("finish_reason", "stop")

        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)

        return ProviderResponse(
            content=content,
            model=model,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            provider_latency_ms=latency_ms,
            raw_response=data,
        )

    def _handle_error(self, resp: httpx.Response, request_id: str) -> None:
        status = resp.status_code
        try:
            body = resp.json()
            message = body.get("error", {}).get("message", resp.text)
        except Exception:
            message = resp.text

        logger.error(
            "provider_error",
            provider="openai",
            status_code=status,
            message=message[:200],
            request_id=request_id,
        )

        if status in (401, 403):
            raise ProviderAuthError("openai")
        elif status == 429:
            raise ProviderRateLimitError("openai")
        elif status >= 500:
            raise ProviderError(
                message=f"OpenAI server error ({status}): {message[:200]}",
                provider="openai",
                upstream_status=status,
                retryable=True,
            )
        else:
            raise ProviderError(
                message=f"OpenAI error ({status}): {message[:200]}",
                provider="openai",
                upstream_status=status,
                retryable=False,
            )
