# 09 — Streaming

> SSE streaming protocol, provider streaming translation, disconnect handling, and partial usage recording for InfrGate.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | Phase 3 |
| **Audience** | All contributors |

---

## 1. Overview

InfrGate supports Server-Sent Events (SSE) streaming for chat completions. When a client sends `"stream": true`, the gateway opens an SSE connection and relays token chunks from the upstream provider in real-time.

### Streaming architecture

```text
Client (SSE)     InfrGate Gateway      Provider (native stream)
    │                   │                        │
    │  POST stream=true │                        │
    │──────────────────▶│                        │
    │                   │  POST streamGenerate   │
    │                   │───────────────────────▶│
    │                   │                        │
    │  data: chunk1     │◀─── native chunk 1 ────│
    │◀──────────────────│                        │
    │                   │                        │
    │  data: chunk2     │◀─── native chunk 2 ────│
    │◀──────────────────│                        │
    │                   │                        │
    │  ...              │   ...                  │
    │                   │                        │
    │  data: [DONE]     │◀─── stream end ────────│
    │◀──────────────────│                        │
    │                   │                        │
    │                   │  Record usage          │
    │                   │──────────────────────▶ DB
```

---

## 2. SSE protocol

### 2.1 Response headers

```
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Request-ID: 550e8400-e29b-41d4-a716-446655440000
```

### 2.2 Chunk format

Each chunk follows the OpenAI streaming format:

```
data: {"id":"chatcmpl-<request_id>","object":"chat.completion.chunk","created":1700000000,"model":"gemini-2.0-flash","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-<request_id>","object":"chat.completion.chunk","created":1700000000,"model":"gemini-2.0-flash","choices":[{"index":0,"delta":{"content":"The"},"finish_reason":null}]}

data: {"id":"chatcmpl-<request_id>","object":"chat.completion.chunk","created":1700000000,"model":"gemini-2.0-flash","choices":[{"index":0,"delta":{"content":" capital"},"finish_reason":null}]}

data: {"id":"chatcmpl-<request_id>","object":"chat.completion.chunk","created":1700000000,"model":"gemini-2.0-flash","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]

```

### 2.3 Chunk schema

```python
@dataclass
class StreamChunk:
    """A single chunk in the SSE stream."""
    id: str                            # chatcmpl-<request_id>
    object: str = "chat.completion.chunk"
    created: int = 0                   # Unix timestamp
    model: str = ""
    delta_role: str | None = None      # Only on first chunk
    delta_content: str | None = None   # Token content
    finish_reason: str | None = None   # null until final chunk
    usage: dict | None = None          # Optional: included in final chunk
```

### 2.4 First and last chunks

**First chunk:** includes `delta.role = "assistant"` with empty content:

```json
{"choices": [{"delta": {"role": "assistant", "content": ""}, "finish_reason": null}]}
```

**Final chunk:** includes `finish_reason` and optionally `usage`:

```json
{"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17}}
```

**Sentinel:** `data: [DONE]` signals the end of the stream.

---

## 3. Provider streaming translation

### 3.1 Gemini streaming

**Endpoint:** `POST /models/{model}:streamGenerateContent?alt=sse&key={API_KEY}`

**Gemini SSE chunk format:**

```
data: {"candidates":[{"content":{"parts":[{"text":"The"}],"role":"model"}}]}

data: {"candidates":[{"content":{"parts":[{"text":" capital"}],"role":"model"}}],"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":7,"totalTokenCount":17}}
```

**Translation to OpenAI format:**

```python
async def stream(self, request: ProviderRequest) -> AsyncIterator[StreamChunk]:
    url = f"{self.BASE_URL}/models/{request.model}:streamGenerateContent"
    params = {"key": self._api_key, "alt": "sse"}
    body = self._translate_request(request)

    async with self._client.stream("POST", url, json=body, params=params) as response:
        is_first = True
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue

            data = json.loads(line[6:])
            candidate = data.get("candidates", [{}])[0]
            content = candidate.get("content", {})
            text = content.get("parts", [{}])[0].get("text", "")
            finish = self._map_finish_reason(candidate.get("finishReason"))
            usage_meta = data.get("usageMetadata")

            chunk = StreamChunk(
                id=f"chatcmpl-{request.request_id}",
                model=request.model,
                created=int(time.time()),
                delta_role="assistant" if is_first else None,
                delta_content=text,
                finish_reason=finish,
            )

            if usage_meta and finish:
                chunk.usage = {
                    "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                    "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
                    "total_tokens": usage_meta.get("totalTokenCount", 0),
                }

            is_first = False
            yield chunk
```

### 3.2 OpenAI streaming `[Phase 2]`

OpenAI's streaming format is already OpenAI-compatible. The adapter passes chunks through with minimal transformation (adding InfrGate's request ID).

---

## 4. Gateway streaming handler

### 4.1 FastAPI SSE implementation

```python
from fastapi.responses import StreamingResponse

@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    tenant: Tenant = Depends(get_current_tenant),
):
    if request.stream:
        return StreamingResponse(
            _stream_response(request, tenant),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Request-ID": request.state.request_id,
            },
        )
    else:
        # Non-streaming path (Phase 1)
        ...

async def _stream_response(
    request: ChatCompletionRequest,
    tenant: Tenant,
) -> AsyncIterator[str]:
    """Generate SSE events from provider stream."""
    usage_tracker = StreamUsageTracker(request_id=request.state.request_id)

    try:
        async for chunk in provider.stream(provider_request):
            usage_tracker.update(chunk)

            # Format as SSE event
            event_data = chunk.to_openai_dict()
            yield f"data: {json.dumps(event_data)}\n\n"

        # Stream completed successfully
        yield "data: [DONE]\n\n"

        # Record usage
        await record_usage(usage_tracker.finalize(), tenant)

    except ProviderError as e:
        # Stream failed mid-way
        logger.error("stream_error", error=str(e), request_id=request.state.request_id)

        # Record partial usage
        await record_usage(usage_tracker.finalize(status="failed"), tenant)

        # Send error event and close
        error_event = {"error": {"type": "provider_error", "message": str(e)}}
        yield f"data: {json.dumps(error_event)}\n\n"
        yield "data: [DONE]\n\n"

    except asyncio.CancelledError:
        # Client disconnected
        logger.info("stream_client_disconnect", request_id=request.state.request_id)

        # Record partial usage
        await record_usage(usage_tracker.finalize(status="partial"), tenant)
        raise
```

---

## 5. Client disconnect handling

### 5.1 Detection

When a client closes the SSE connection, FastAPI/Starlette raises `asyncio.CancelledError` in the streaming generator. The gateway catches this to:

1. Stop consuming from the upstream provider
2. Record partial usage with `status = "partial"`
3. Log the disconnect event

### 5.2 Upstream cancellation

When the client disconnects, the gateway should also cancel the upstream provider request to avoid wasting resources:

```python
async def _stream_response(...):
    provider_stream = None
    try:
        provider_stream = provider.stream(provider_request)
        async for chunk in provider_stream:
            ...
    except asyncio.CancelledError:
        # Cancel upstream if possible
        if provider_stream and hasattr(provider_stream, 'aclose'):
            await provider_stream.aclose()
        raise
```

---

## 6. Stream usage tracking

### 6.1 Usage tracker

The usage tracker accumulates token counts during streaming:

```python
class StreamUsageTracker:
    """Tracks usage statistics during a streaming response."""

    def __init__(self, request_id: str):
        self.request_id = request_id
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.chunks_received = 0
        self.content_buffer = []
        self.start_time = time.monotonic()
        self.finish_reason: str | None = None

    def update(self, chunk: StreamChunk):
        self.chunks_received += 1
        if chunk.delta_content:
            self.content_buffer.append(chunk.delta_content)
        if chunk.finish_reason:
            self.finish_reason = chunk.finish_reason
        if chunk.usage:
            self.prompt_tokens = chunk.usage.get("prompt_tokens", 0)
            self.completion_tokens = chunk.usage.get("completion_tokens", 0)
            self.total_tokens = chunk.usage.get("total_tokens", 0)

    def finalize(self, status: str = "completed") -> UsageRecord:
        latency_ms = int((time.monotonic() - self.start_time) * 1000)

        # If provider didn't report tokens, estimate from content
        if self.total_tokens == 0 and self.content_buffer:
            estimated_completion = _estimate_tokens("".join(self.content_buffer))
            self.completion_tokens = estimated_completion
            self.total_tokens = self.prompt_tokens + estimated_completion

        return UsageRecord(
            request_id=self.request_id,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
            status=status,
            latency_ms=latency_ms,
            metadata={
                "chunks_received": self.chunks_received,
                "finish_reason": self.finish_reason,
                "client_disconnected": status == "partial",
            },
        )
```

---

## 7. Backpressure and buffering

### 7.1 Strategy

- **No buffering:** Chunks are forwarded as soon as they are received from the provider. There is no intermediate buffer that accumulates chunks.
- **Backpressure:** If the client is slow to consume, Python's async generator backpressure naturally slows down consumption from the provider (await in `aiter_lines` blocks).
- **Timeout between chunks:** If no chunk arrives from the provider within `stream_read_timeout_s`, the stream is terminated with an error.

### 7.2 Inter-chunk timeout

```python
async def _stream_with_timeout(
    provider_stream: AsyncIterator[StreamChunk],
    inter_chunk_timeout: float = 10.0,
) -> AsyncIterator[StreamChunk]:
    """Wrap a provider stream with inter-chunk timeout."""
    async for chunk in provider_stream:
        try:
            yield await asyncio.wait_for(
                _next_chunk(provider_stream),
                timeout=inter_chunk_timeout,
            )
        except asyncio.TimeoutError:
            raise ProviderTimeoutError("provider", inter_chunk_timeout)
```

---

## 8. Core invariant: stream accounting

> **Every stream produces a usage record**, regardless of outcome.

| Outcome | Usage status | Tokens recorded |
|---|---|---|
| Complete success | `completed` | Provider-reported tokens |
| Provider error mid-stream | `failed` | Best-known partial tokens |
| Client disconnect | `partial` | Accumulated tokens at disconnect |
| Timeout between chunks | `failed` | Accumulated tokens at timeout |

This satisfies the core invariant from [About.md](../../About.md): _"Disconnected or failed streams still produce a usage record with best-known state."_

---

## References

- [02-api-design.md](02-api-design.md) — Streaming request/response format
- [06-provider-adapters.md](06-provider-adapters.md) — Provider streaming interface
- [10-usage-accounting.md](10-usage-accounting.md) — Usage ledger recording
