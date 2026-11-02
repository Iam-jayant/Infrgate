"""
InfrGate client — OpenAI-compatible interface for chat completions.

Provides both synchronous (`InfrGateClient`) and asynchronous
(`AsyncInfrGateClient`) clients that mirror the OpenAI SDK's
`.chat.completions.create()` interface.

Usage::

    from infrgate_sdk import InfrGateClient

    client = InfrGateClient(
        base_url="https://infrgate.onrender.com",
        api_key="your-api-key",
    )

    # Non-streaming
    response = client.chat.completions.create(
        model="Qwen/Qwen2.5-72B-Instruct",
        messages=[{"role": "user", "content": "Hello!"}],
    )

    # Streaming
    for chunk in client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "Write a poem."}],
        stream=True,
    ):
        print(chunk.choices[0].delta.content, end="")
"""

from __future__ import annotations

from typing import Any, Literal, overload

import httpx

from infrgate_sdk._http import AsyncTransport, SyncTransport
from infrgate_sdk.models import ChatCompletionResponse, ChatMessage
from infrgate_sdk.streaming import AsyncStream, Stream


# ─────────────────────────────────────────────────────────────────────────────
# Sync Client
# ─────────────────────────────────────────────────────────────────────────────


class _SyncCompletions:
    """Synchronous `.chat.completions` resource."""

    def __init__(self, transport: SyncTransport):
        self._transport = transport

    @overload
    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str] | ChatMessage],
        temperature: float = ...,
        max_tokens: int | None = ...,
        top_p: float = ...,
        stop: str | list[str] | None = ...,
        stream: Literal[False] = ...,
        idempotency_key: str | None = ...,
    ) -> ChatCompletionResponse: ...

    @overload
    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str] | ChatMessage],
        temperature: float = ...,
        max_tokens: int | None = ...,
        top_p: float = ...,
        stop: str | list[str] | None = ...,
        stream: Literal[True],
        idempotency_key: str | None = ...,
    ) -> Stream: ...

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str] | ChatMessage],
        temperature: float = 1.0,
        max_tokens: int | None = None,
        top_p: float = 1.0,
        stop: str | list[str] | None = None,
        stream: bool = False,
        idempotency_key: str | None = None,
    ) -> ChatCompletionResponse | Stream:
        """
        Create a chat completion.

        Args:
            model: Model identifier (e.g., "Qwen/Qwen2.5-72B-Instruct").
            messages: Conversation messages as dicts or ChatMessage objects.
            temperature: Sampling temperature (0.0 to 2.0).
            max_tokens: Maximum tokens to generate.
            top_p: Nucleus sampling parameter.
            stop: Stop sequence(s).
            stream: If True, returns a Stream iterator of chunks.
            idempotency_key: Optional client-supplied idempotency key.

        Returns:
            ChatCompletionResponse for non-streaming, or Stream for streaming.
        """
        # Normalize messages to dicts
        normalized: list[dict[str, str]] = []
        for msg in messages:
            if isinstance(msg, ChatMessage):
                normalized.append(msg.model_dump())
            else:
                normalized.append(msg)

        body: dict[str, Any] = {
            "model": model,
            "messages": normalized,
            "temperature": temperature,
            "top_p": top_p,
            "stream": stream,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if stop is not None:
            body["stop"] = stop

        extra_headers: dict[str, str] = {}
        if idempotency_key:
            extra_headers["Idempotency-Key"] = idempotency_key

        if stream:
            raw_lines = self._transport.stream_request(
                "POST",
                "/v1/chat/completions",
                json=body,
                headers=extra_headers or None,
            )
            return Stream(raw_lines)

        data = self._transport.request(
            "POST",
            "/v1/chat/completions",
            json=body,
            headers=extra_headers or None,
            idempotent=True,
        )
        return ChatCompletionResponse.model_validate(data)


class _SyncChat:
    """Synchronous `.chat` namespace."""

    def __init__(self, transport: SyncTransport):
        self.completions = _SyncCompletions(transport)


class InfrGateClient:
    """
    Synchronous InfrGate client.

    Usage::

        client = InfrGateClient(
            base_url="https://infrgate.onrender.com",
            api_key="your-tenant-api-key",
        )
        response = client.chat.completions.create(
            model="Qwen/Qwen2.5-72B-Instruct",
            messages=[{"role": "user", "content": "Hello!"}],
        )
        print(response.choices[0].message.content)
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: httpx.Timeout | None = None,
        max_retries: int = 2,
        default_headers: dict[str, str] | None = None,
    ):
        self._transport = SyncTransport(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
        )
        self.chat = _SyncChat(self._transport)

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ─────────────────────────────────────────────────────────────────────────────
# Async Client
# ─────────────────────────────────────────────────────────────────────────────


class _AsyncCompletions:
    """Asynchronous `.chat.completions` resource."""

    def __init__(self, transport: AsyncTransport):
        self._transport = transport

    @overload
    async def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str] | ChatMessage],
        temperature: float = ...,
        max_tokens: int | None = ...,
        top_p: float = ...,
        stop: str | list[str] | None = ...,
        stream: Literal[False] = ...,
        idempotency_key: str | None = ...,
    ) -> ChatCompletionResponse: ...

    @overload
    async def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str] | ChatMessage],
        temperature: float = ...,
        max_tokens: int | None = ...,
        top_p: float = ...,
        stop: str | list[str] | None = ...,
        stream: Literal[True],
        idempotency_key: str | None = ...,
    ) -> AsyncStream: ...

    async def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str] | ChatMessage],
        temperature: float = 1.0,
        max_tokens: int | None = None,
        top_p: float = 1.0,
        stop: str | list[str] | None = None,
        stream: bool = False,
        idempotency_key: str | None = None,
    ) -> ChatCompletionResponse | AsyncStream:
        """
        Create a chat completion (async).

        Same parameters as the sync version.
        """
        normalized: list[dict[str, str]] = []
        for msg in messages:
            if isinstance(msg, ChatMessage):
                normalized.append(msg.model_dump())
            else:
                normalized.append(msg)

        body: dict[str, Any] = {
            "model": model,
            "messages": normalized,
            "temperature": temperature,
            "top_p": top_p,
            "stream": stream,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if stop is not None:
            body["stop"] = stop

        extra_headers: dict[str, str] = {}
        if idempotency_key:
            extra_headers["Idempotency-Key"] = idempotency_key

        if stream:
            raw_lines = self._transport.stream_request(
                "POST",
                "/v1/chat/completions",
                json=body,
                headers=extra_headers or None,
            )
            return AsyncStream(raw_lines)

        data = await self._transport.request(
            "POST",
            "/v1/chat/completions",
            json=body,
            headers=extra_headers or None,
            idempotent=True,
        )
        return ChatCompletionResponse.model_validate(data)


class _AsyncChat:
    """Asynchronous `.chat` namespace."""

    def __init__(self, transport: AsyncTransport):
        self.completions = _AsyncCompletions(transport)


class AsyncInfrGateClient:
    """
    Asynchronous InfrGate client.

    Usage::

        async with AsyncInfrGateClient(
            base_url="https://infrgate.onrender.com",
            api_key="your-tenant-api-key",
        ) as client:
            response = await client.chat.completions.create(
                model="Qwen/Qwen2.5-72B-Instruct",
                messages=[{"role": "user", "content": "Hello!"}],
            )
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: httpx.Timeout | None = None,
        max_retries: int = 2,
        default_headers: dict[str, str] | None = None,
    ):
        self._transport = AsyncTransport(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
        )
        self.chat = _AsyncChat(self._transport)

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._transport.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
