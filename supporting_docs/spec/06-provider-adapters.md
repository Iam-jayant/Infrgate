# 06 — Provider Adapters

> Provider interface, adapter implementations, registry, and model mapping for InfrGate.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | Phase 1 (Gemini), Phase 2 (OpenAI, provider interface, registry) |
| **Audience** | All contributors |

---

## 1. Overview

Provider adapters translate between InfrGate's internal request/response model (OpenAI-compatible) and each upstream provider's native API. Every adapter implements a common interface so the routing engine and reliability layer are provider-agnostic.

### Architecture

```text
                    ┌───────────────────┐
                    │  Routing Engine   │
                    └────────┬──────────┘
                             │
                    ┌────────▼──────────┐
                    │ Provider Registry │
                    └────────┬──────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │   Gemini     │ │   OpenAI     │ │    Fake      │
    │   Adapter    │ │   Adapter    │ │   Adapter    │
    │  [Phase 1]   │ │  [Phase 2]   │ │  [Phase 2]   │
    └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
           │                │                │
           ▼                ▼                ▼
    Google Gemini     OpenAI API      In-memory
       API                            (testing)
```

---

## 2. Provider interface `[Phase 2 — formalized; Phase 1 uses simplified version]`

### 2.1 Abstract base class

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ProviderRequest:
    """Normalized request to a provider."""
    model: str                          # Provider-native model ID
    messages: list[dict]                # OpenAI-format messages
    temperature: float = 1.0
    max_tokens: int | None = None
    top_p: float = 1.0
    stop: str | list[str] | None = None
    stream: bool = False
    request_id: str = ""                # For correlation

@dataclass
class ProviderResponse:
    """Normalized response from a provider."""
    content: str                        # Generated text
    model: str                          # Model that responded
    finish_reason: str                  # "stop", "length", "content_filter"
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    provider_latency_ms: int            # Upstream latency
    raw_response: dict | None = None    # Original provider response (debug)

class ProviderAdapter(ABC):
    """
    Abstract base class for all provider adapters.
    Each adapter translates between InfrGate's internal model
    and a provider's native API.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique identifier for this provider (e.g., 'gemini', 'openai')."""
        ...

    @property
    @abstractmethod
    def supported_models(self) -> list[str]:
        """List of model IDs this provider supports."""
        ...

    @abstractmethod
    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """
        Execute a non-streaming chat completion.
        Raises ProviderError on failure.
        """
        ...

    @abstractmethod
    async def stream(self, request: ProviderRequest) -> AsyncIterator[StreamChunk]:
        """
        Execute a streaming chat completion. [Phase 3]
        Yields StreamChunk objects.
        Raises ProviderError on failure.
        """
        ...

    def translate_model(self, openai_model: str) -> str:
        """
        Map an OpenAI-compatible model name to this provider's native model ID.
        Default: return as-is. Override for providers with different naming.
        """
        return openai_model
```

### 2.2 Error hierarchy

```python
class ProviderError(Exception):
    """Base exception for all provider errors."""
    def __init__(
        self,
        message: str,
        provider: str,
        status_code: int | None = None,
        retryable: bool = False,
        raw_response: dict | None = None,
    ):
        self.message = message
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable
        self.raw_response = raw_response

class ProviderTimeoutError(ProviderError):
    """Upstream request timed out."""
    def __init__(self, provider: str, timeout_seconds: float):
        super().__init__(
            f"Request to {provider} timed out after {timeout_seconds}s",
            provider=provider,
            retryable=True,
        )

class ProviderRateLimitError(ProviderError):
    """Upstream provider rate limited us."""
    def __init__(self, provider: str, retry_after: int | None = None):
        super().__init__(
            f"Rate limited by {provider}",
            provider=provider,
            status_code=429,
            retryable=True,
        )
        self.retry_after = retry_after

class ProviderAuthError(ProviderError):
    """Upstream provider rejected our credentials."""
    def __init__(self, provider: str):
        super().__init__(
            f"Authentication failed with {provider}",
            provider=provider,
            status_code=401,
            retryable=False,
        )
```

---

## 3. Gemini adapter `[Phase 1]`

### 3.1 API details

| Property | Value |
|---|---|
| **API** | Google Generative Language API (v1beta) |
| **Base URL** | `https://generativelanguage.googleapis.com/v1beta` |
| **Auth** | API key as query parameter (`?key=API_KEY`) |
| **Endpoint** | `POST /models/{model}:generateContent` |
| **Streaming endpoint** | `POST /models/{model}:streamGenerateContent` `[Phase 3]` |
| **Free tier** | Yes — via Google AI Studio |

### 3.2 Model mapping

| InfrGate model ID | Gemini native ID |
|---|---|
| `gemini-2.0-flash` | `gemini-2.0-flash` |
| `gemini-2.5-flash` | `gemini-2.5-flash` |
| `gemini-2.5-pro` | `gemini-2.5-pro` |

### 3.3 Request translation

**InfrGate (OpenAI-compatible) → Gemini native:**

```python
# OpenAI-format input
{
    "model": "gemini-2.0-flash",
    "messages": [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"}
    ],
    "temperature": 0.7,
    "max_tokens": 256
}

# Translated to Gemini format
{
    "system_instruction": {
        "parts": [{"text": "You are helpful."}]
    },
    "contents": [
        {
            "role": "user",
            "parts": [{"text": "Hello"}]
        }
    ],
    "generationConfig": {
        "temperature": 0.7,
        "maxOutputTokens": 256,
        "topP": 1.0
    }
}
```

**Translation rules:**

| OpenAI field | Gemini field | Notes |
|---|---|---|
| `messages[role=system]` | `system_instruction.parts[].text` | Extracted and moved to top level |
| `messages[role=user]` | `contents[].role = "user"` | Direct mapping |
| `messages[role=assistant]` | `contents[].role = "model"` | Role name change |
| `temperature` | `generationConfig.temperature` | Direct mapping |
| `max_tokens` | `generationConfig.maxOutputTokens` | Field name change |
| `top_p` | `generationConfig.topP` | Direct mapping |
| `stop` | `generationConfig.stopSequences` | String → array if needed |

### 3.4 Response translation

**Gemini native → InfrGate (OpenAI-compatible):**

```python
# Gemini response
{
    "candidates": [{
        "content": {
            "parts": [{"text": "Hello! How can I help?"}],
            "role": "model"
        },
        "finishReason": "STOP"
    }],
    "usageMetadata": {
        "promptTokenCount": 10,
        "candidatesTokenCount": 7,
        "totalTokenCount": 17
    }
}

# Translated to OpenAI format
{
    "id": "chatcmpl-<request_id>",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gemini-2.0-flash",
    "choices": [{
        "index": 0,
        "message": {
            "role": "assistant",
            "content": "Hello! How can I help?"
        },
        "finish_reason": "stop"
    }],
    "usage": {
        "prompt_tokens": 10,
        "completion_tokens": 7,
        "total_tokens": 17
    }
}
```

**Finish reason mapping:**

| Gemini | OpenAI |
|---|---|
| `STOP` | `stop` |
| `MAX_TOKENS` | `length` |
| `SAFETY` | `content_filter` |
| `RECITATION` | `content_filter` |
| Other | `stop` (with warning log) |

### 3.5 Gemini error handling

| Gemini HTTP status | Classification | Action |
|---|---|---|
| `200` | Success | Return translated response |
| `400` | Invalid request | Raise `ProviderError(retryable=False)` |
| `401` / `403` | Auth error | Raise `ProviderAuthError` |
| `429` | Rate limited | Raise `ProviderRateLimitError` |
| `500` | Server error | Raise `ProviderError(retryable=True)` |
| `503` | Service unavailable | Raise `ProviderError(retryable=True)` |
| Timeout | Timeout | Raise `ProviderTimeoutError` |

### 3.6 Implementation outline

```python
class GeminiAdapter(ProviderAdapter):
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, http_client: httpx.AsyncClient):
        self._api_key = api_key
        self._client = http_client

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def supported_models(self) -> list[str]:
        return ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"]

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        url = f"{self.BASE_URL}/models/{request.model}:generateContent"
        body = self._translate_request(request)

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

        latency_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            self._handle_error(resp)

        return self._translate_response(resp.json(), request.model, latency_ms)

    def _translate_request(self, request: ProviderRequest) -> dict:
        # ... translation logic as described above
        ...

    def _translate_response(self, data: dict, model: str, latency_ms: int) -> ProviderResponse:
        # ... translation logic as described above
        ...
```

---

## 4. OpenAI adapter `[Phase 2]`

### 4.1 API details

| Property | Value |
|---|---|
| **API** | OpenAI Chat Completions API (v1) |
| **Base URL** | `https://api.openai.com/v1` |
| **Auth** | `Authorization: Bearer <OPENAI_API_KEY>` |
| **Endpoint** | `POST /chat/completions` |

### 4.2 Model mapping

| InfrGate model ID | OpenAI native ID |
|---|---|
| `gpt-4o` | `gpt-4o` |
| `gpt-4o-mini` | `gpt-4o-mini` |

### 4.3 Request translation

Minimal — InfrGate's client API is already OpenAI-compatible. The adapter passes the request nearly as-is, adding authentication:

```python
class OpenAIAdapter(ProviderAdapter):
    BASE_URL = "https://api.openai.com/v1"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        resp = await self._client.post(
            f"{self.BASE_URL}/chat/completions",
            json={
                "model": request.model,
                "messages": request.messages,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "top_p": request.top_p,
                "stop": request.stop,
                "stream": False,
            },
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        # Response is already OpenAI-compatible — minimal translation
        ...
```

---

## 5. Fake adapter `[Phase 2]`

The fake adapter is used for testing, development, and **failure injection**. It returns predictable responses and can be configured to simulate errors.

### 5.1 Capabilities

| Capability | Description |
|---|---|
| Fixed response | Return a predetermined response for any request |
| Latency simulation | Configurable delay before responding |
| Error injection | Return specific error types on command |
| Rate limit simulation | Return 429 after N requests |
| Timeout simulation | Hang until timeout fires |
| Partial streaming | Stream N chunks then fail `[Phase 3]` |

### 5.2 Configuration

```python
class FakeAdapter(ProviderAdapter):
    def __init__(
        self,
        response_text: str = "This is a test response.",
        latency_ms: int = 100,
        fail_after: int | None = None,     # Fail after N requests
        error_type: str | None = None,     # "timeout", "rate_limit", "server_error"
        tokens: tuple[int, int] = (10, 5), # (prompt, completion) token counts
    ): ...
```

---

## 6. Provider registry `[Phase 2]`

The registry manages all available provider adapters and provides lookup by name or model.

### 6.1 Interface

```python
class ProviderRegistry:
    """Registry of all available provider adapters."""

    def register(self, adapter: ProviderAdapter) -> None:
        """Register a provider adapter."""
        ...

    def get_by_name(self, provider_name: str) -> ProviderAdapter:
        """Get adapter by provider name. Raises if not found."""
        ...

    def get_by_model(self, model: str) -> list[ProviderAdapter]:
        """Get all adapters that support the given model, ordered by priority."""
        ...

    def list_providers(self) -> list[ProviderInfo]:
        """List all registered providers with their status."""
        ...
```

### 6.2 Registration (at startup)

```python
async def create_provider_registry(settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry()

    # Always register Gemini (Phase 1+)
    http_client = httpx.AsyncClient()
    registry.register(GeminiAdapter(settings.GEMINI_API_KEY, http_client))

    # Register OpenAI if key is configured (Phase 2+)
    if settings.OPENAI_API_KEY:
        registry.register(OpenAIAdapter(settings.OPENAI_API_KEY, http_client))

    return registry
```

### 6.3 Phase 1 simplification

In Phase 1, there is no formal registry. The Gemini adapter is directly instantiated and used. The registry abstraction is introduced in Phase 2 when multiple providers need to be managed.

```python
# Phase 1: Direct adapter usage
gemini = GeminiAdapter(settings.GEMINI_API_KEY, http_client)
response = await gemini.complete(provider_request)

# Phase 2+: Registry-based lookup
adapters = registry.get_by_model(requested_model)
response = await reliability_layer.execute(adapters, provider_request)
```

---

## 7. HTTP client management

### 7.1 Connection pooling

All adapters share a single `httpx.AsyncClient` instance with connection pooling:

```python
http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(
        connect=5.0,       # Connection establishment
        read=30.0,         # Waiting for response
        write=10.0,        # Sending request
        pool=10.0,         # Waiting for a connection from the pool
    ),
    limits=httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20,
    ),
    follow_redirects=True,
)
```

### 7.2 Lifecycle

The HTTP client is created at application startup and closed at shutdown:

```python
@app.on_event("startup")
async def startup():
    app.state.http_client = httpx.AsyncClient(...)
    app.state.provider_registry = await create_provider_registry(settings)

@app.on_event("shutdown")
async def shutdown():
    await app.state.http_client.aclose()
```

---

## References

- [02-api-design.md](02-api-design.md) — Chat completion request/response format
- [07-routing-engine.md](07-routing-engine.md) — How providers are selected
- [08-reliability.md](08-reliability.md) — Timeout, retry, circuit breaker around provider calls
- [09-streaming.md](09-streaming.md) — Streaming adapter interface
