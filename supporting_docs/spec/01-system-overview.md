# 01 — System Overview

> High-Level Design (HLD) for InfrGate — the intelligent inference control plane.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | All (1–5) |
| **Audience** | All contributors, reviewers |

---

## 1. Purpose

InfrGate is a backend inference gateway that sits between client applications and LLM providers. It exposes a single **OpenAI-compatible API** (`POST /v1/chat/completions`) and handles authentication, tenant isolation, rate limiting, provider routing, reliability, streaming, usage accounting, and observability.

The system is designed as a **modular monolith** that can scale horizontally. It is not a collection of microservices.

---

## 2. System context

```text
┌─────────────────────────────────────────────────────────────┐
│                        Clients                              │
│   (Any app using OpenAI-compatible SDK or raw HTTP)         │
└───────────────────────────┬─────────────────────────────────┘
                            │
              OpenAI-compatible HTTP API
              Authorization: Bearer <api-key>
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                      InfrGate                               │
│                                                             │
│  ┌──────────┐  ┌───────────┐  ┌────────────┐  ┌─────────┐   │
│  │  Auth &  │→ │  Policy   │→ │  Routing   │→ │ Provider│   │
│  │ Tenancy  │  │ Enforce   │  │  Engine    │  │ Adapter │   │
│  └──────────┘  └───────────┘  └────────────┘  └────┬────┘   │
│                                                     │       │
│  ┌──────────────────────────────────────────────────┘       │
│  │                                                          │
│  │  ┌──────────┐  ┌───────────┐  ┌────────────────────┐     │
│  └→ │  Usage   │  │  Worker   │  │  Observability     │     │
│     │ Recorder │  │  (async)  │  │  (logs/metrics)    │     │
│     └─────┬────┘  └───── ┬────┘  └────────────────────┘     │
│           │              │                                  │
│     ┌─────▼──────────────▼──────┐    ┌──────────┐           │
│     │      PostgreSQL           │    │   Redis  │           │
│     │  (system of record)       │    │ (ephemeral│          │
│     │                           │    │  state)  │           │
│     └───────────────────────────┘    └──────────┘           │
└─────────────────────────────────────────────────────────────┘
                            │
              Provider-specific APIs
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
    ┌─────────┐       ┌─────────┐       ┌──────────┐
    │ Google  │       │ OpenAI  │       │  Future  │
    │ Gemini  │       │   API   │       │ Providers│
    │[Phase 1]│       │[Phase 2]│       │ [later]  │
    └─────────┘       └─────────┘       └──────────┘
```

---

## 3. Component boundaries

Each component has strict ownership. This prevents coupling and makes testing tractable.

| Component | Owns | Must NOT own |
|---|---|---|
| **Gateway (FastAPI)** | HTTP interface, request validation, ASGI lifecycle, middleware orchestration, request ID generation | Provider-specific logic, billing calculation |
| **Auth & Tenancy** | API key verification, tenant resolution, tenant context injection, plan lookup | Rate limit enforcement, provider credentials |
| **Policy Enforcement** | Spend-cap check, model authorization against plan, pre-flight policy gates | Provider communication, usage recording |
| **Rate Limiter** | Token-bucket / sliding-window enforcement, rate limit headers, Redis coordination | Auth, tenant resolution |
| **Routing Engine** | Model → provider resolution, priority-based selection, health-aware scoring `[Phase 4]`, routing decision log | HTTP transport, response translation |
| **Provider Adapters** | Upstream authentication, request/response format translation, streaming translation, model name mapping | Tenant policies, routing decisions, usage aggregation |
| **Reliability Layer** | Timeout enforcement, retry scheduling, circuit breaker FSM, failover orchestration | Usage recording, response formatting |
| **Usage Service** | Durable usage record creation, idempotency enforcement, token accounting | Webhook delivery, aggregation scheduling |
| **Background Worker** | Job dequeuing, usage aggregation, webhook delivery, retry/dead-letter management `[Phase 3]` | Blocking the inference hot path |
| **Observability** | Structured logging, health endpoints, Prometheus metrics `[Phase 5]` | Business logic, request handling |

---

## 4. Request lifecycle

The complete request lifecycle when all phases are active:

```text
1. HTTP request arrives
   └─→ Uvicorn / ASGI

2. Middleware: Request ID
   └─→ Generate or accept X-Request-ID
   └─→ Attach to request state and logger context

3. Middleware: Authentication                              [Phase 1]
   └─→ Extract Bearer token from Authorization header
   └─→ Prefix lookup → hash verification → load tenant
   └─→ Reject: 401 Unauthorized

4. Policy enforcement                                      [Phase 1]
   ├─→ Tenant status check (active/suspended)
   ├─→ Model authorization against tenant plan
   └─→ Spend-cap check (current_spend < spend_cap)
       └─→ Reject: 403 Forbidden / 429 Spend Cap Exceeded

5. Rate limiting                                           [Phase 1]
   └─→ Redis sliding window check (per-tenant, per-plan)
       └─→ Reject: 429 Too Many Requests (+ Retry-After)

6. Routing decision                                        [Phase 2+]
   ├─→ Resolve requested model to eligible providers
   ├─→ Filter by health state (circuit breaker)            [Phase 2]
   ├─→ Score by EWMA health + cost + capability            [Phase 4]
   └─→ Select provider; log routing decision

7. Provider execution                                      [Phase 1+]
   ├─→ Translate request to provider-native format
   ├─→ Apply timeout                                       [Phase 2]
   ├─→ Execute HTTP call (non-streaming or SSE)
   ├─→ On failure: retry with backoff                      [Phase 2]
   ├─→ On persistent failure: circuit breaker trip          [Phase 2]
   ├─→ On circuit open: failover to next provider          [Phase 2]
   └─→ Translate response to OpenAI-compatible format

8. Usage recording                                         [Phase 1]
   ├─→ Extract token counts from response
   ├─→ INSERT into usage_ledger (idempotent on request_id)
   └─→ Update tenant spend accumulator

9. Response delivery
   ├─→ Non-streaming: JSON response body
   └─→ Streaming: SSE chunks with [DONE] sentinel         [Phase 3]

10. Background processing                                  [Phase 3]
    ├─→ Enqueue aggregation job
    └─→ Enqueue webhook if spend threshold crossed
```

---

## 5. Deployment model

### 5.1 Local development (Docker Compose)

```text
docker-compose.yml
├── gateway        FastAPI + Uvicorn (port 8000)
├── postgres       PostgreSQL 16 (port 5432)
├── redis          Redis 7 (port 6379)
└── worker         Background worker process              [Phase 3+]
```

All services share a Docker bridge network. The gateway connects to Postgres and Redis via service names.

### 5.2 Production growth path

```text
                    ┌──────────────┐
                    │ Load Balancer│
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌─────────┐ ┌─────────┐ ┌─────────┐
         │Gateway 1│ │Gateway 2│ │Gateway N│
         └────┬────┘ └────┬────┘ └────┬────┘
              │            │            │
              └────────────┼────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌─────────┐ ┌─────────┐ ┌─────────┐
         │PostgreSQL│ │  Redis  │ │Worker(s)│
         │ (primary │ │ (cluster│ │         │
         │  + read  │ │  or     │ │         │
         │ replicas)│ │ sentinel│ │         │
         └─────────┘ └─────────┘ └─────────┘
```

**Scaling rules:**
- Gateway is stateless → scale horizontally behind a load balancer
- Worker scales independently; concurrency controlled by `FOR UPDATE SKIP LOCKED`
- PostgreSQL: single primary for writes; read replicas for analytics (future)
- Redis: single instance sufficient for moderate load; Sentinel/Cluster for HA

---

## 6. Phase overlay

What is **active** at each phase completion:

| Component | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|:---:|:---:|:---:|:---:|:---:|
| FastAPI Gateway | ✅ | ✅ | ✅ | ✅ | ✅ |
| PostgreSQL | ✅ | ✅ | ✅ | ✅ | ✅ |
| Redis | ✅ | ✅ | ✅ | ✅ | ✅ |
| Auth & Tenancy | ✅ | ✅ | ✅ | ✅ | ✅ |
| Rate Limiting | ✅ | ✅ | ✅ | ✅ | ✅ |
| Gemini Adapter | ✅ | ✅ | ✅ | ✅ | ✅ |
| Usage Ledger | ✅ | ✅ | ✅ | ✅ | ✅ |
| OpenAI Adapter | — | ✅ | ✅ | ✅ | ✅ |
| Provider Registry | — | ✅ | ✅ | ✅ | ✅ |
| Routing Engine | — | ✅ | ✅ | ✅ | ✅ |
| Timeout / Retry | — | ✅ | ✅ | ✅ | ✅ |
| Circuit Breaker | — | ✅ | ✅ | ✅ | ✅ |
| Failover | — | ✅ | ✅ | ✅ | ✅ |
| SSE Streaming | — | — | ✅ | ✅ | ✅ |
| Background Worker | — | — | ✅ | ✅ | ✅ |
| Webhooks | — | — | ✅ | ✅ | ✅ |
| EWMA Health Scoring | — | — | — | ✅ | ✅ |
| Cost-Aware Routing | — | — | — | ✅ | ✅ |
| Health Endpoints | — | — | — | — | ✅ |
| Prometheus Metrics | — | — | — | — | ✅ |
| Load Tests | — | — | — | — | ✅ |

---

## 7. Technology decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Language** | Python 3.12+ | Async ecosystem, FastAPI maturity, LLM tooling ecosystem |
| **Framework** | FastAPI | Native async, Pydantic integration, auto OpenAPI docs |
| **ASGI server** | Uvicorn | Production-grade, works with FastAPI out of the box |
| **Database** | PostgreSQL | ACID guarantees for usage ledger, rich indexing, `FOR UPDATE SKIP LOCKED` for job queue |
| **Cache** | Redis | Sub-millisecond rate limit checks, ephemeral circuit breaker state, lightweight pub/sub potential |
| **Migrations** | Alembic | Standard SQLAlchemy migration tool, supports autogenerate |
| **HTTP client** | httpx (async) | Async-first, streaming support, timeout control, connection pooling |
| **Validation** | Pydantic v2 | Performance improvement over v1, native FastAPI integration |
| **Testing** | pytest + pytest-asyncio | Async test support, fixture system, widespread adoption |
| **Containers** | Docker + Compose | Reproducible local environment, single-command startup |

---

## 8. Cross-cutting concerns

### 8.1 Request ID

Every request receives a unique `request_id` (UUID v4). If the client sends `X-Request-ID`, the gateway uses it (after validation); otherwise, the gateway generates one. The `request_id` is:

- Included in every log line
- Returned in the `X-Request-ID` response header
- Used as the idempotency key for usage recording

### 8.2 Error model

All errors return a consistent JSON envelope:

```json
{
  "error": {
    "type": "rate_limit_exceeded",
    "message": "Rate limit exceeded. Retry after 2 seconds.",
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "code": 429
  }
}
```

Error types are enumerated in [02-api-design.md](02-api-design.md#error-types).

### 8.3 Configuration

Configuration is environment-variable-driven with sensible defaults, loaded via Pydantic `BaseSettings`:

```python
class Settings(BaseSettings):
    # Database
    DATABASE_URL: str
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    # Provider keys
    GEMINI_API_KEY: str
    OPENAI_API_KEY: str | None = None          # Phase 2
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    # Rate limiting
    DEFAULT_RPM: int = 60
    DEFAULT_TPM: int = 100_000
```

---

## References

- [About.md](../../About.md) — Project overview and phase breakdown
- [02-api-design.md](02-api-design.md) — Full API contract
- [03-data-model.md](03-data-model.md) — Database schema
