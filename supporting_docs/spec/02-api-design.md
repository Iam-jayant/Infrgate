# 02 — API Design

> Complete API contract for InfrGate across all phases.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | All (1–5) |
| **Audience** | All contributors, client integrators |

---

## 1. Conventions

### 1.1 Base URL

```
http://localhost:8000
```

Production deployments sit behind a load balancer with TLS termination; the gateway itself listens on plain HTTP internally.

### 1.2 Authentication

All client-facing endpoints require a Bearer token:

```
Authorization: Bearer sk-infr_<prefix>.<secret>
```

Admin endpoints use the same mechanism but require a tenant with `role = admin`. See [04-authentication-tenancy.md](04-authentication-tenancy.md).

### 1.3 Common headers

| Header | Direction | Description |
|---|---|---|
| `Authorization` | Request | `Bearer <api-key>` — required on all endpoints |
| `Content-Type` | Request | `application/json` |
| `X-Request-ID` | Request (optional) | Client-supplied correlation ID (UUID v4). If absent, gateway generates one |
| `X-Request-ID` | Response | Always returned; echoes client-supplied or gateway-generated ID |
| `X-RateLimit-Limit` | Response | Maximum requests allowed in the current window `[Phase 1]` |
| `X-RateLimit-Remaining` | Response | Requests remaining in the current window `[Phase 1]` |
| `X-RateLimit-Reset` | Response | Unix timestamp when the current window resets `[Phase 1]` |
| `Content-Type` | Response | `application/json` or `text/event-stream` (streaming) `[Phase 3]` |

### 1.4 Timestamps

All timestamps are **ISO 8601 UTC** with timezone designator:

```
2026-01-15T10:30:00Z
```

### 1.5 IDs

All entity IDs are **UUID v4**, represented as lowercase hyphenated strings:

```
550e8400-e29b-41d4-a716-446655440000
```

---

## 2. Endpoint catalog

### Overview

| Method | Path | Phase | Description |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | 1 | Chat completion (non-streaming) |
| `POST` | `/v1/chat/completions` | 3 | Chat completion (streaming via `stream: true`) |
| `POST` | `/admin/tenants` | 1 | Create a new tenant |
| `GET` | `/admin/tenants` | 1 | List all tenants |
| `GET` | `/admin/tenants/{tenant_id}` | 1 | Get tenant details |
| `PATCH` | `/admin/tenants/{tenant_id}` | 1 | Update tenant |
| `POST` | `/admin/tenants/{tenant_id}/api-keys` | 1 | Create API key for tenant |
| `GET` | `/admin/tenants/{tenant_id}/api-keys` | 1 | List API keys for tenant |
| `DELETE` | `/admin/api-keys/{key_id}` | 1 | Revoke an API key |
| `GET` | `/admin/usage` | 1 | Query usage records |
| `GET` | `/admin/usage/summary` | 1 | Aggregated usage summary |
| `GET` | `/admin/providers` | 2 | List configured providers and health |
| `GET` | `/admin/routing/decisions` | 4 | Recent routing decision log |
| `GET` | `/health/live` | 5 | Liveness probe |
| `GET` | `/health/ready` | 5 | Readiness probe |
| `GET` | `/metrics` | 5 | Prometheus-style metrics |

---

## 3. Endpoint details

### 3.1 Chat completions `[Phase 1]`

**`POST /v1/chat/completions`**

OpenAI-compatible chat completion endpoint. The request and response shapes conform to the OpenAI Chat Completions API so existing SDKs and tools work without modification.

#### Request body

```json
{
  "model": "gemini-2.0-flash",
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful assistant."
    },
    {
      "role": "user",
      "content": "What is the capital of France?"
    }
  ],
  "temperature": 0.7,
  "max_tokens": 256,
  "stream": false
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `model` | `string` | Yes | — | Model identifier. Gateway resolves to a provider |
| `messages` | `array[Message]` | Yes | — | Conversation messages |
| `messages[].role` | `string` | Yes | — | One of: `system`, `user`, `assistant` |
| `messages[].content` | `string` | Yes | — | Message content |
| `temperature` | `float` | No | `1.0` | Sampling temperature (0.0–2.0) |
| `max_tokens` | `integer` | No | Provider default | Maximum tokens to generate |
| `top_p` | `float` | No | `1.0` | Nucleus sampling parameter |
| `stream` | `boolean` | No | `false` | Enable SSE streaming `[Phase 3]` |
| `n` | `integer` | No | `1` | Number of completions (only `1` supported) |
| `stop` | `string \| array` | No | `null` | Stop sequences |

#### Response body (non-streaming)

```json
{
  "id": "chatcmpl-550e8400-e29b-41d4-a716-446655440000",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "gemini-2.0-flash",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "The capital of France is Paris."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 8,
    "total_tokens": 33
  }
}
```

| Field | Type | Description |
|---|---|---|
| `id` | `string` | Unique completion ID (prefixed `chatcmpl-` + request_id) |
| `object` | `string` | Always `"chat.completion"` |
| `created` | `integer` | Unix timestamp of completion creation |
| `model` | `string` | Model that generated the response |
| `choices` | `array[Choice]` | Completion choices (always length 1 in Phase 1) |
| `choices[].index` | `integer` | Choice index |
| `choices[].message` | `Message` | Assistant response message |
| `choices[].finish_reason` | `string` | `"stop"`, `"length"`, or `"content_filter"` |
| `usage` | `Usage` | Token usage statistics |
| `usage.prompt_tokens` | `integer` | Tokens in the prompt |
| `usage.completion_tokens` | `integer` | Tokens in the completion |
| `usage.total_tokens` | `integer` | Total tokens consumed |

#### Response body (streaming) `[Phase 3]`

When `stream: true`, the response is an SSE stream. See [09-streaming.md](09-streaming.md) for the full streaming protocol.

Each chunk:

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1700000000,"model":"gemini-2.0-flash","choices":[{"index":0,"delta":{"content":"The"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1700000000,"model":"gemini-2.0-flash","choices":[{"index":0,"delta":{"content":" capital"},"finish_reason":null}]}

data: [DONE]
```

---

### 3.2 Tenant management `[Phase 1]`

#### Create tenant

**`POST /admin/tenants`**

```json
// Request
{
  "name": "Acme Corp",
  "plan": "standard",
  "spend_cap_cents": 100000,
  "config": {
    "allowed_models": ["gemini-2.0-flash", "gemini-2.5-pro"],
    "rpm_limit": 60,
    "tpm_limit": 100000
  }
}

// Response (201 Created)
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Acme Corp",
  "plan": "standard",
  "status": "active",
  "spend_cap_cents": 100000,
  "current_spend_cents": 0,
  "config": {
    "allowed_models": ["gemini-2.0-flash", "gemini-2.5-pro"],
    "rpm_limit": 60,
    "tpm_limit": 100000
  },
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-01-15T10:30:00Z"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | `string` | Yes | Human-readable tenant name |
| `plan` | `string` | Yes | Plan tier: `free`, `standard`, `enterprise` |
| `spend_cap_cents` | `integer` | No | Monthly spend cap in cents (null = unlimited) |
| `config` | `TenantConfig` | No | Override defaults for rate limits and allowed models |

#### List tenants

**`GET /admin/tenants`**

Query parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `status` | `string` | `null` | Filter by status: `active`, `suspended` |
| `limit` | `integer` | `50` | Page size (max 100) |
| `offset` | `integer` | `0` | Pagination offset |

Response: `{ "items": [Tenant], "total": integer, "limit": integer, "offset": integer }`

#### Get tenant

**`GET /admin/tenants/{tenant_id}`**

Returns a single tenant object.

#### Update tenant

**`PATCH /admin/tenants/{tenant_id}`**

Partial update. Only supplied fields are modified.

```json
// Request
{
  "status": "suspended",
  "spend_cap_cents": 50000
}

// Response (200 OK)
{ /* Updated tenant object */ }
```

---

### 3.3 API key management `[Phase 1]`

#### Create API key

**`POST /admin/tenants/{tenant_id}/api-keys`**

```json
// Request
{
  "name": "production-key"
}

// Response (201 Created)
{
  "id": "key-uuid",
  "tenant_id": "tenant-uuid",
  "name": "production-key",
  "prefix": "sk-infr_abc12345",
  "key": "sk-infr_abc12345.full_secret_shown_only_once",
  "created_at": "2026-01-15T10:30:00Z",
  "revoked_at": null
}
```

> **Important:** The full `key` value is returned **only on creation**. It is never stored or retrievable again. The client must save it securely.

#### List API keys

**`GET /admin/tenants/{tenant_id}/api-keys`**

Returns keys with `prefix` visible but `key` (secret) redacted.

#### Revoke API key

**`DELETE /admin/api-keys/{key_id}`**

Sets `revoked_at` to current timestamp. Revoked keys immediately fail authentication.

---

### 3.4 Usage queries `[Phase 1]`

#### Query usage records

**`GET /admin/usage`**

Query parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tenant_id` | `uuid` | `null` | Filter by tenant |
| `model` | `string` | `null` | Filter by model |
| `start_date` | `datetime` | 30 days ago | Start of time range (inclusive) |
| `end_date` | `datetime` | now | End of time range (inclusive) |
| `limit` | `integer` | `50` | Page size (max 100) |
| `offset` | `integer` | `0` | Pagination offset |

Response:

```json
{
  "items": [
    {
      "id": "uuid",
      "request_id": "uuid",
      "tenant_id": "uuid",
      "model": "gemini-2.0-flash",
      "provider": "gemini",
      "prompt_tokens": 25,
      "completion_tokens": 8,
      "total_tokens": 33,
      "cost_cents": 0,
      "status": "completed",
      "latency_ms": 450,
      "created_at": "2026-01-15T10:30:00Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

#### Usage summary

**`GET /admin/usage/summary`**

Query parameters: same as `/admin/usage` (except `limit`/`offset`).

Response:

```json
{
  "tenant_id": "uuid",
  "period_start": "2026-01-01T00:00:00Z",
  "period_end": "2026-01-31T23:59:59Z",
  "total_requests": 1500,
  "total_prompt_tokens": 150000,
  "total_completion_tokens": 45000,
  "total_tokens": 195000,
  "total_cost_cents": 2340,
  "by_model": {
    "gemini-2.0-flash": {
      "requests": 1200,
      "total_tokens": 150000,
      "cost_cents": 1200
    },
    "gpt-4o": {
      "requests": 300,
      "total_tokens": 45000,
      "cost_cents": 1140
    }
  }
}
```

---

### 3.5 Provider management `[Phase 2]`

#### List providers

**`GET /admin/providers`**

```json
{
  "providers": [
    {
      "name": "gemini",
      "status": "healthy",
      "circuit_state": "closed",
      "models": ["gemini-2.0-flash", "gemini-2.5-pro"],
      "priority": 1,
      "error_rate_1h": 0.02,
      "avg_latency_ms": 320
    },
    {
      "name": "openai",
      "status": "healthy",
      "circuit_state": "closed",
      "models": ["gpt-4o", "gpt-4o-mini"],
      "priority": 2,
      "error_rate_1h": 0.01,
      "avg_latency_ms": 450
    }
  ]
}
```

---

### 3.6 Routing decisions `[Phase 4]`

#### Recent routing decisions

**`GET /admin/routing/decisions`**

Query parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | `integer` | `20` | Number of recent decisions |
| `tenant_id` | `uuid` | `null` | Filter by tenant |

```json
{
  "decisions": [
    {
      "request_id": "uuid",
      "requested_model": "gpt-4o",
      "eligible_providers": ["openai", "gemini"],
      "selected_provider": "openai",
      "reason": "highest_priority",
      "scores": {
        "openai": { "health": 0.98, "latency": 0.85, "cost": 0.6, "total": 0.88 },
        "gemini": { "health": 0.95, "latency": 0.90, "cost": 0.9, "total": 0.82 }
      },
      "fallback_used": false,
      "timestamp": "2026-01-15T10:30:00Z"
    }
  ]
}
```

---

### 3.7 Health endpoints `[Phase 5]`

#### Liveness probe

**`GET /health/live`**

No authentication required.

```json
// 200 OK
{ "status": "ok" }
```

Returns `200` if the process is running. Does not check dependencies.

#### Readiness probe

**`GET /health/ready`**

No authentication required.

```json
// 200 OK
{
  "status": "ready",
  "checks": {
    "postgres": "ok",
    "redis": "ok",
    "providers": {
      "gemini": "healthy",
      "openai": "healthy"
    }
  }
}

// 503 Service Unavailable
{
  "status": "not_ready",
  "checks": {
    "postgres": "ok",
    "redis": "error",
    "providers": {
      "gemini": "healthy",
      "openai": "unhealthy"
    }
  }
}
```

Returns `200` if the gateway can serve requests (Postgres + Redis connected). Returns `503` if a critical dependency is unavailable.

---

### 3.8 Metrics `[Phase 5]`

**`GET /metrics`**

Prometheus text exposition format. No authentication required.

```
# HELP infrgate_requests_total Total inference requests
# TYPE infrgate_requests_total counter
infrgate_requests_total{tenant="acme",model="gemini-2.0-flash",status="success"} 1523

# HELP infrgate_request_duration_seconds Request duration histogram
# TYPE infrgate_request_duration_seconds histogram
infrgate_request_duration_seconds_bucket{le="0.1"} 120
infrgate_request_duration_seconds_bucket{le="0.5"} 980
infrgate_request_duration_seconds_bucket{le="1.0"} 1400
infrgate_request_duration_seconds_bucket{le="+Inf"} 1523

# HELP infrgate_tokens_total Total tokens consumed
# TYPE infrgate_tokens_total counter
infrgate_tokens_total{tenant="acme",type="prompt"} 150000
infrgate_tokens_total{tenant="acme",type="completion"} 45000

# HELP infrgate_provider_circuit_state Circuit breaker state (0=closed, 1=open, 2=half_open)
# TYPE infrgate_provider_circuit_state gauge
infrgate_provider_circuit_state{provider="gemini"} 0
infrgate_provider_circuit_state{provider="openai"} 0
```

See [12-observability.md](12-observability.md) for the full metrics catalog.

---

## 4. Error model

### 4.1 Error response envelope

All errors return a consistent JSON structure:

```json
{
  "error": {
    "type": "error_type_identifier",
    "message": "Human-readable description of what went wrong.",
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "code": 400
  }
}
```

### 4.2 Error types

| HTTP Status | Error Type | Phase | Description |
|---|---|---|---|
| `400` | `invalid_request` | 1 | Malformed request body, missing required fields, invalid parameter values |
| `401` | `authentication_required` | 1 | Missing or malformed `Authorization` header |
| `401` | `invalid_api_key` | 1 | API key not found, revoked, or hash mismatch |
| `403` | `tenant_suspended` | 1 | Tenant account is suspended |
| `403` | `model_not_allowed` | 1 | Requested model is not in the tenant's plan |
| `403` | `spend_cap_exceeded` | 1 | Tenant has exceeded their spend cap |
| `404` | `not_found` | 1 | Resource not found |
| `429` | `rate_limit_exceeded` | 1 | Per-tenant rate limit exceeded. Includes `Retry-After` header |
| `500` | `internal_error` | 1 | Unexpected server error |
| `502` | `provider_error` | 1 | Upstream provider returned an error |
| `503` | `provider_unavailable` | 2 | All eligible providers are unavailable (circuit open) |
| `504` | `provider_timeout` | 2 | Upstream provider request timed out after all retries |

### 4.3 Rate limit error details

When `429 rate_limit_exceeded` is returned, the response includes additional headers:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 2
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1700000060
```

---

## 5. Pagination

All list endpoints use offset-based pagination:

| Parameter | Type | Default | Max | Description |
|---|---|---|---|---|
| `limit` | `integer` | `50` | `100` | Number of items per page |
| `offset` | `integer` | `0` | — | Number of items to skip |

Response envelope:

```json
{
  "items": [ /* ... */ ],
  "total": 250,
  "limit": 50,
  "offset": 100
}
```

---

## References

- [01-system-overview.md](01-system-overview.md) — System architecture
- [04-authentication-tenancy.md](04-authentication-tenancy.md) — Authentication flow
- [09-streaming.md](09-streaming.md) — Streaming protocol
- [12-observability.md](12-observability.md) — Metrics catalog
