"""
Tests for InfrGateClient — chat completions (sync).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from infrgate_sdk import (
    AuthenticationError,
    ChatCompletionResponse,
    InfrGateClient,
    PermissionDeniedError,
    RateLimitError,
    Stream,
)

BASE_URL = "https://test.infrgate.dev"
API_KEY = "test-prefix.test-secret-key"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_completion_response() -> dict:
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "Qwen/Qwen2.5-72B-Instruct",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello! How can I help?"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    }


@pytest.fixture
def mock_stream_lines() -> list[str]:
    chunks = [
        {"id": "chatcmpl-abc123", "object": "chat.completion.chunk", "created": 1700000000, "model": "gemini-2.5-flash", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        {"id": "chatcmpl-abc123", "object": "chat.completion.chunk", "created": 1700000000, "model": "gemini-2.5-flash", "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}]},
        {"id": "chatcmpl-abc123", "object": "chat.completion.chunk", "created": 1700000000, "model": "gemini-2.5-flash", "choices": [{"index": 0, "delta": {"content": " world"}, "finish_reason": None}]},
        {"id": "chatcmpl-abc123", "object": "chat.completion.chunk", "created": 1700000000, "model": "gemini-2.5-flash", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    lines.append("data: [DONE]\n\n")
    return lines


# ── Non-Streaming Tests ──────────────────────────────────────────────────────


@respx.mock
def test_create_completion(mock_completion_response):
    """Non-streaming chat completion returns a typed ChatCompletionResponse."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=mock_completion_response)
    )

    with InfrGateClient(base_url=BASE_URL, api_key=API_KEY) as client:
        response = client.chat.completions.create(
            model="Qwen/Qwen2.5-72B-Instruct",
            messages=[{"role": "user", "content": "Hello!"}],
        )

    assert isinstance(response, ChatCompletionResponse)
    assert response.id == "chatcmpl-abc123"
    assert response.choices[0].message.content == "Hello! How can I help?"
    assert response.usage.total_tokens == 18


@respx.mock
def test_create_completion_with_params(mock_completion_response):
    """All optional parameters are correctly passed in the request body."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=mock_completion_response)
    )

    with InfrGateClient(base_url=BASE_URL, api_key=API_KEY) as client:
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Hi"}],
            temperature=0.5,
            max_tokens=100,
            top_p=0.9,
            stop=["END"],
        )

    body = json.loads(route.calls[0].request.content)
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 100
    assert body["top_p"] == 0.9
    assert body["stop"] == ["END"]
    assert body["stream"] is False


@respx.mock
def test_auth_header_sent():
    """Authorization header is correctly set as Bearer token."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x", "object": "chat.completion", "created": 0,
                "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )
    )

    with InfrGateClient(base_url=BASE_URL, api_key=API_KEY) as client:
        client.chat.completions.create(
            model="m",
            messages=[{"role": "user", "content": "Hi"}],
        )

    assert route.calls[0].request.headers["Authorization"] == f"Bearer {API_KEY}"


@respx.mock
def test_idempotency_key_auto_generated():
    """An Idempotency-Key header is auto-generated when not supplied."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x", "object": "chat.completion", "created": 0,
                "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )
    )

    with InfrGateClient(base_url=BASE_URL, api_key=API_KEY) as client:
        client.chat.completions.create(
            model="m",
            messages=[{"role": "user", "content": "Hi"}],
        )

    assert "Idempotency-Key" in route.calls[0].request.headers
    assert route.calls[0].request.headers["Idempotency-Key"].startswith("sdk-")


@respx.mock
def test_custom_idempotency_key():
    """A client-supplied idempotency key is used instead of auto-generated."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x", "object": "chat.completion", "created": 0,
                "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )
    )

    with InfrGateClient(base_url=BASE_URL, api_key=API_KEY) as client:
        client.chat.completions.create(
            model="m",
            messages=[{"role": "user", "content": "Hi"}],
            idempotency_key="my-custom-key-123",
        )

    assert route.calls[0].request.headers["Idempotency-Key"] == "my-custom-key-123"


# ── Error Handling Tests ─────────────────────────────────────────────────────


@respx.mock
def test_401_raises_authentication_error():
    """401 from gateway raises AuthenticationError."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            401,
            json={"error": {"type": "invalid_api_key", "message": "Invalid API key.", "code": 401}},
        )
    )

    with InfrGateClient(base_url=BASE_URL, api_key="bad-key", max_retries=0) as client:
        with pytest.raises(AuthenticationError) as exc_info:
            client.chat.completions.create(
                model="m",
                messages=[{"role": "user", "content": "Hi"}],
            )

    assert exc_info.value.status_code == 401
    assert "Invalid API key" in exc_info.value.message


@respx.mock
def test_403_raises_permission_denied():
    """403 from gateway raises PermissionDeniedError."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            403,
            json={"error": {"type": "model_not_allowed", "message": "Model not on plan.", "code": 403}},
        )
    )

    with InfrGateClient(base_url=BASE_URL, api_key=API_KEY, max_retries=0) as client:
        with pytest.raises(PermissionDeniedError):
            client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": "Hi"}],
            )


@respx.mock
def test_429_raises_rate_limit_error():
    """429 from gateway raises RateLimitError (after retries exhausted)."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            429,
            json={"error": {"type": "rate_limit_exceeded", "message": "Rate limited.", "code": 429}},
            headers={"Retry-After": "5"},
        )
    )

    with InfrGateClient(base_url=BASE_URL, api_key=API_KEY, max_retries=0) as client:
        with pytest.raises(RateLimitError) as exc_info:
            client.chat.completions.create(
                model="m",
                messages=[{"role": "user", "content": "Hi"}],
            )

    assert exc_info.value.retry_after == 5


# ── Streaming Tests ──────────────────────────────────────────────────────────


@respx.mock
def test_streaming_completion(mock_stream_lines):
    """Streaming returns a Stream iterator that yields typed chunks."""
    sse_content = "".join(mock_stream_lines)
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=sse_content.encode(),
            headers={"Content-Type": "text/event-stream"},
        )
    )

    with InfrGateClient(base_url=BASE_URL, api_key=API_KEY) as client:
        stream = client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "Hi"}],
            stream=True,
        )

    assert isinstance(stream, Stream)


# ── Context Manager Tests ─────────────────────────────────────────────────


def test_client_context_manager():
    """Client works as a context manager."""
    with InfrGateClient(base_url=BASE_URL, api_key=API_KEY) as client:
        assert client is not None
        assert hasattr(client, "chat")
        assert hasattr(client.chat, "completions")
