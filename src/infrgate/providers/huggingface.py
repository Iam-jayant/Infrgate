"""
Hugging Face adapter — translates Hugging Face-compatible requests to Hugging Face Inference API.
"""

from __future__ import annotations

import json
import time
from typing import AsyncIterator

import httpx
import structlog

from infrgate.exceptions import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from infrgate.providers.base import ProviderAdapter, ProviderRequest, ProviderResponse
from infrgate.schemas.streaming import StreamChunk

logger = structlog.get_logger()


class HuggingFaceAdapter(ProviderAdapter):
    """Provider adapter for Hugging Face."""

    BASE_URL = "https://api.huggingface.com/v1"

    SUPPORTED_MODELS = [
        "meta-llama/Llama-3.2-3B-Instruct",
        "Qwen/Qwen2.5-72B-Instruct",
    ]

    def __init__(self, api_key: str, http_client: httpx.AsyncClient):
        self._api_key = api_key
        self._client = http_client

    @property
    def provider_name(self) -> str:
        return "huggingface"

    @property
    def supported_models(self) -> list[str]:
        return self.SUPPORTED_MODELS

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        url = "https://router.huggingface.co/v1/chat/completions"
        body = self._translate_request(request)

        logger.info(
            "provider_call",
            provider="huggingface",
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
            raise ProviderTimeoutError("huggingface", 30.0)
        except httpx.ConnectError as e:
            raise ProviderError(
                message=f"Failed to connect to Hugging Face: {e}",
                provider="huggingface",
                retryable=True,
            )

        latency_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            self._handle_error(resp, request.request_id)

        data = resp.json()
        response = self._translate_response(data, request.model, latency_ms)

        logger.info(
            "provider_response",
            provider="huggingface",
            model=request.model,
            latency_ms=latency_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            request_id=request.request_id,
        )

        return response

    async def stream(self, request: ProviderRequest) -> AsyncIterator[StreamChunk]:
        url = "https://router.huggingface.co/v1/chat/completions"
        body = self._translate_request(request)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}

        logger.info(
            "provider_stream_started",
            provider="huggingface",
            model=request.model,
            request_id=request.request_id,
        )

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with self._client.stream(
                "POST",
                url,
                json=body,
                headers=headers,
                timeout=30.0,
            ) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    self._handle_error(resp, request.request_id)

                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line == "data: [DONE]":
                        break
                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)

                            choices = data.get("choices", [])
                            delta_role = None
                            delta_content = None
                            finish_reason = None

                            if choices:
                                choice = choices[0]
                                delta = choice.get("delta", {})
                                delta_role = delta.get("role")
                                delta_content = delta.get("content")
                                finish_reason = choice.get("finish_reason")

                            usage = data.get("usage")

                            yield StreamChunk(
                                id=request.request_id,
                                model=request.model,
                                delta_role=delta_role,
                                delta_content=delta_content,
                                finish_reason=finish_reason,
                                usage=usage
                            )
                        except json.JSONDecodeError:
                            logger.warning(
                                "provider_stream_parse_error",
                                provider="huggingface",
                                line=line,
                                request_id=request.request_id
                            )

        except httpx.TimeoutException:
            raise ProviderTimeoutError("huggingface", 30.0)
        except httpx.ConnectError as e:
            raise ProviderError(
                message=f"Failed to connect to Hugging Face: {e}",
                provider="huggingface",
                retryable=True,
            )

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
                message="Hugging Face returned no choices.",
                provider="huggingface",
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
            provider="huggingface",
            status_code=status,
            message=message[:200],
            request_id=request_id,
        )

        if status in (401, 403):
            raise ProviderAuthError("huggingface")
        elif status == 429:
            raise ProviderRateLimitError("huggingface")
        elif status >= 500:
            raise ProviderError(
                message=f"Hugging Face server error ({status}): {message[:200]}",
                provider="huggingface",
                upstream_status=status,
                retryable=True,
            )
        else:
            raise ProviderError(
                message=f"Hugging Face error ({status}): {message[:200]}",
                provider="huggingface",
                upstream_status=status,
                retryable=False,
            )
