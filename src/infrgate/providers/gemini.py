"""
Gemini adapter — translates OpenAI-compatible requests to Google Gemini API.

Handles request/response format translation, model mapping, error
classification, and token extraction from Gemini's native response.

Spec reference: 06-provider-adapters.md §3
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

_FINISH_REASON_MAP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "OTHER": "stop",
}


class GeminiAdapter(ProviderAdapter):
    """Provider adapter for Google Gemini (Generative Language API)."""

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    SUPPORTED_MODELS = [
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ]

    def __init__(self, api_key: str, http_client: httpx.AsyncClient):
        self._api_key = api_key
        self._client = http_client

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def supported_models(self) -> list[str]:
        return self.SUPPORTED_MODELS

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Execute a non-streaming chat completion via Gemini."""
        url = f"{self.BASE_URL}/models/{request.model}:generateContent"
        body = self._translate_request(request)

        logger.info(
            "provider_call",
            provider="gemini",
            model=request.model,
            request_id=request.request_id,
        )

        start = time.monotonic()
        try:
            resp = await self._client.post(
                url,
                json=body,
                params={"key": self._api_key},
                timeout=30.0,
            )
        except httpx.TimeoutException:
            raise ProviderTimeoutError("gemini", 30.0)
        except httpx.ConnectError as e:
            raise ProviderError(
                message=f"Failed to connect to Gemini: {e}",
                provider="gemini",
                retryable=True,
            )

        latency_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            self._handle_error(resp, request.request_id)

        data = resp.json()
        response = self._translate_response(data, request.model, latency_ms)

        logger.info(
            "provider_response",
            provider="gemini",
            model=request.model,
            latency_ms=latency_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            request_id=request.request_id,
        )

        return response

    def _translate_request(self, request: ProviderRequest) -> dict:
        """
        Translate an OpenAI-compatible request to Gemini format.

        Mapping:
          - system messages → system_instruction.parts[].text
          - user/assistant messages → contents[].parts[].text
          - role "assistant" → role "model"
          - temperature, max_tokens, top_p → generationConfig
        """
        system_parts = []
        contents = []

        for msg in request.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_parts.append({"text": content})
            else:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append({
                    "role": gemini_role,
                    "parts": [{"text": content}],
                })

        body: dict = {"contents": contents}

        if system_parts:
            body["system_instruction"] = {"parts": system_parts}

        generation_config: dict = {}
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.max_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_tokens
        if request.top_p is not None:
            generation_config["topP"] = request.top_p
        if request.stop:
            stops = [request.stop] if isinstance(request.stop, str) else request.stop
            generation_config["stopSequences"] = stops

        if generation_config:
            body["generationConfig"] = generation_config

        return body

    def _translate_response(
        self,
        data: dict,
        model: str,
        latency_ms: int,
    ) -> ProviderResponse:
        """
        Translate a Gemini response to the OpenAI-compatible internal model.

        Extracts content, finish reason, and usage metadata from the
        Gemini response format.
        """
        candidates = data.get("candidates", [])
        if not candidates:
            raise ProviderError(
                message="Gemini returned no candidates.",
                provider="gemini",
                retryable=False,
                raw_response=data,
            )

        candidate = candidates[0]
        content_obj = candidate.get("content", {})
        parts = content_obj.get("parts", [])
        content = parts[0].get("text", "") if parts else ""

        gemini_finish = candidate.get("finishReason", "STOP")
        finish_reason = _FINISH_REASON_MAP.get(gemini_finish, "stop")

        if gemini_finish not in _FINISH_REASON_MAP:
            logger.warning(
                "unknown_finish_reason",
                provider="gemini",
                finish_reason=gemini_finish,
            )

        usage = data.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)
        total_tokens = usage.get("totalTokenCount", 0)

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
        """
        Classify Gemini HTTP errors and raise appropriate exceptions.

        Error classification per spec §3.5:
          400 → non-retryable (bad request)
          401/403 → auth error (non-retryable)
          429 → rate limit (retryable)
          500/503 → server error (retryable)
        """
        status = resp.status_code
        try:
            body = resp.json()
            message = body.get("error", {}).get("message", resp.text)
        except Exception:
            message = resp.text

        logger.error(
            "provider_error",
            provider="gemini",
            status_code=status,
            message=message[:200],
            request_id=request_id,
        )

        if status in (401, 403):
            raise ProviderAuthError("gemini")
        elif status == 429:
            raise ProviderRateLimitError("gemini")
        elif status >= 500:
            raise ProviderError(
                message=f"Gemini server error ({status}): {message[:200]}",
                provider="gemini",
                upstream_status=status,
                retryable=True,
            )
        else:
            raise ProviderError(
                message=f"Gemini error ({status}): {message[:200]}",
                provider="gemini",
                upstream_status=status,
                retryable=False,
            )
