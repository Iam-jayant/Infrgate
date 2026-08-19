# InfrGate — Project Overview

**InfrGate** is an intelligent inference control plane for routing, reliability, usage metering, and policy enforcement across multiple LLM providers.

It sits between client applications and model providers behind a single OpenAI-compatible API, so teams integrate with InfrGate instead of building auth, quotas, failover, and billing logic in every product.

The **client contract** is OpenAI-compatible (`POST /v1/chat/completions`). That does **not** require an OpenAI account. Phase 1 talks to **Google Gemini** (free API key from [Google AI Studio](https://aistudio.google.com/apikey)). OpenAI and other providers are added as adapters in later phases.

---

## Problem

Applications integrating multiple LLM providers repeatedly solve the same infrastructure problems:

- API authentication and tenant isolation
- Provider selection and routing
- Provider failures, retries, and failover
- Rate limits and spend caps
- Streaming and usage accounting
- Observability and operational safety

InfrGate centralizes those concerns behind one backend gateway.

---

## Product thesis

InfrGate is **not** a generic LLM proxy. It is a production-minded **backend control plane** where reliability, routing, tenant isolation, and usage accounting are first-class concerns.

> Build the smallest coherent production-style inference gateway first, then progressively add intelligence and sophistication.

---

## Tech stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12+ |
| API framework | FastAPI |
| ASGI server | Uvicorn |
| Database | PostgreSQL (system of record) |
| Cache / distributed state | Redis (rate limits, ephemeral coordination) |
| Migrations | Alembic |
| Validation | Pydantic v2 |
| HTTP client | httpx (async) |
| Testing | pytest |
| Containerization | Docker + Docker Compose |

---

## Core invariants

These rules apply across all phases:

| Invariant | Description |
|-----------|-------------|
| **Tenant isolation** | A request must never access another tenant's keys, usage, quotas, or policy |
| **Ledger uniqueness** | Every inference has a unique `request_id`; usage is persisted at most once |
| **Policy before provider call** | Auth, quota, and model-policy checks run before any upstream request |
| **Failure containment** | A failed provider must not cause indefinite waits or repeated routing to a known-bad provider |
| **Stream accounting** | Disconnected or failed streams still produce a usage record with best-known state |

---

## Full system architecture

Target architecture when all phases are complete:

```text
                           ┌───────────────────┐
                           │   Client Apps     │
                           └─────────┬─────────┘
                                     │
                     OpenAI-compatible client API
                                     │
                           ┌─────────▼─────────┐
                           │  FastAPI Gateway  │
                           └─────────┬─────────┘
                                     │
                  ┌──────────────────┼──────────────────┐
                  │                  │                  │
                  ▼                  ▼                  ▼
             Auth/Tenant       Rate Limiter        Policy Checks
                  │                  │                  │
                  └──────────────────┼──────────────────┘
                                     ▼
                           ┌─────────────────┐
                           │ Routing Engine  │
                           └───────┬─────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
                 Gemini         OpenAI        Additional
               (Phase 1)      (Phase 2)      (later)
                    │              │              │
                    └──────────────┼──────────────┘
                                   ▼
                           Response / Stream
                                   │
                         ┌─────────▼────────┐
                         │ Usage Recorder  │
                         └─────────┬────────┘
                                   │
                        ┌──────────▼──────────┐
                        │     PostgreSQL      │
                        └─────────────────────┘

                 Redis ────── rate limits / cache / circuit & health state

                 Worker ───── usage aggregation / webhooks
```

### Request lifecycle

```text
HTTP request
    ↓
request ID
    ↓
authentication → tenant resolution → plan / model authorization
    ↓
rate limit → spend-cap check
    ↓
routing decision
    ↓
provider execution (timeout, retry, circuit breaker, failover)
    ↓
response / stream
    ↓
usage persistence
    ↓
background processing (when required)
```

### Logical component boundaries

| Component | Owns | Must not own |
|-----------|------|--------------|
| **Gateway** | HTTP interface, validation, auth, tenant context, rate limiting, orchestration | Provider retry logic, billing rules inside handlers |
| **Routing engine** | Model/provider resolution, eligible selection, health signals, routing logs | HTTP streaming, tenant credentials |
| **Provider adapters** | Upstream auth, request/response translation, streaming translation | Tenant policies, usage rules |
| **Reliability layer** | Timeout, retry, circuit breaker, failover | Usage aggregation |
| **Usage service** | Durable inference accounting, idempotency | Webhook delivery |
| **Worker** | Async jobs: aggregation, webhooks | Blocking the inference hot path |

### Deployment model

**Local / initial:**

```text
Docker Compose
├── FastAPI (gateway)
├── Worker          ← Phase 3+
├── PostgreSQL
└── Redis
```

**Production growth path:**

```text
Load Balancer → Gateway replicas → PostgreSQL + Redis + Worker replicas
```

The API and worker scale independently. The system starts as a **modular monolith plus worker**, not premature microservices.

---

## Phase-wise distribution

InfrGate is built in **five capability phases**. Each phase delivers a usable, testable increment. Features are not pulled forward merely because they sound impressive.

### Phase 1 — Foundation *(current)*

**Goal:** Create the smallest usable InfrGate.

**Included:**

- FastAPI application structure
- PostgreSQL connection and migrations
- Tenant model, API-key authentication, plan configuration, spend-cap field
- Request correlation / request ID
- One provider adapter: **Gemini** (Google Generative Language API; free key via Google AI Studio)
- OpenAI-compatible `POST /v1/chat/completions` (non-streaming) — client-facing shape; upstream is Gemini
- Usage ledger with idempotent `request_id`
- Redis-backed rate limiting
- Basic error model and structured logging
- Unit + integration test foundation
- Docker Compose for local development

**Architecture at phase completion:**

```text
Client → FastAPI → Auth / Tenant → Rate Limit → Gemini Adapter → Usage Ledger
                                                      ↓
                                              PostgreSQL + Redis
```

**Not included:** a second live provider (OpenAI and others land in Phase 2), adaptive routing, circuit breaker, SSE, workers.

**Local setup:** set `GEMINI_API_KEY` from Google AI Studio. No paid OpenAI key is required to run Phase 1.

---

### Phase 2 — Provider Abstraction + Reliability

**Goal:** Turn the single-provider API into a genuine multi-provider gateway.

**Included:**

- Provider interface and registry
- Second live provider (OpenAI) + fake provider for failure injection
- Model/provider resolution (priority-based routing)
- Timeout handling, retry with exponential backoff + jitter
- Circuit breaker (CLOSED → OPEN → HALF_OPEN → CLOSED)
- Provider health state (coarse, from circuit breaker)
- Failover and upstream error normalization

**Apply-ready milestone:** Phase 1 + Phase 2 complete before job applications.

---

### Phase 3 — Streaming + Async Processing

**Goal:** Production-like streaming and durable background work.

**Included:**

- SSE streaming and async provider streaming
- Client disconnect handling and partial usage recording
- Background worker with Postgres job queue (`FOR UPDATE SKIP LOCKED`)
- Usage aggregation and spend-threshold webhook delivery
- Webhook retry and dead-letter behavior

---

### Phase 4 — Intelligent Routing

**Goal:** Explainable heuristic routing using statistical signals — **not machine learning**.

**Included:**

- Rolling error/latency signals and EWMA-based health calculations
- Configurable routing weights (availability, latency, error, cost)
- Cost-aware routing and model capability constraints
- Routing decision logs (explainable inputs and chosen provider)

---

### Phase 5 — Hardening & Production Evidence

**Goal:** Portfolio-quality operational evidence and observability.

**Included:**

- Load tests, concurrency tests, failure-injection suite, benchmarks
- Security hardening and migration safety
- `GET /health/live`, `GET /health/ready`, `GET /metrics` (Prometheus-style)
- Structured logs, API documentation, architecture diagrams, runbook
- Resume claims backed by reproducible test evidence

---

## Phase dependency graph

```text
Phase 1 — Foundation                    ← YOU ARE HERE
   ↓
Phase 2 — Provider Abstraction + Reliability
   ↓
Phase 3 — Streaming + Async Processing
   ↓
Phase 4 — Intelligent Routing
   ↓
Phase 5 — Hardening & Production Evidence
```

---

## Data model (full project)

PostgreSQL is the durable source of truth.

| Table | Purpose | Introduced |
|-------|---------|------------|
| `tenants` | Multi-tenant identity, plan, spend cap, status | Phase 1 |
| `api_keys` | Prefix + hash key storage, revocation | Phase 1 |
| `usage_ledger` | One row per inference; `UNIQUE(request_id)` | Phase 1 |
| `provider_configs` | Provider/model config, priority, costs, timeouts | Phase 2 |
| `jobs` | Postgres-backed worker queue | Phase 3 |
| `webhook_deliveries` | Webhook attempt tracking and retries | Phase 3 |

Redis holds rate-limit counters, circuit breaker state, and ephemeral health/EWMA signals — never the canonical usage ledger.

---

## API surface (full project)

| Endpoint | Phase |
|----------|-------|
| `POST /v1/chat/completions` | Phase 1 (non-streaming); Phase 3 adds streaming |
| `POST /admin/tenants`, `POST /admin/api-keys`, `GET /admin/usage` | Phase 1 |
| `GET /admin/providers`, `GET /admin/health` | Phase 2+ |
| `GET /health/live`, `GET /health/ready` | Phase 5 |
| `GET /metrics` | Phase 5 |

Authentication: `Authorization: Bearer <api-key>`. Every response includes `X-Request-ID`.

---

## Explicit non-goals

The following are **deferred** until the core system is proven:

- Full OpenAI API compatibility
- Generic policy DSL
- Kafka / NATS / RabbitMQ
- Machine-learning-based routing
- Shadow routing and request replay
- Distributed tracing (OpenTelemetry)
- Admin dashboard / large frontend
- Premature microservice decomposition

See [`supporting_docs/spec/13-future-work.md`](../supporting_docs/spec/13-future-work.md) for the full deferred list.

---

## Documentation map

| Document | Location |
|----------|----------|
| Engineering specifications | [`supporting_docs/spec/`](../supporting_docs/spec/) |
| Spec index and contract | [`supporting_docs/README.md`](../supporting_docs/README.md) |
| Project overview (this file) | [`project_docs/ABOUT.md`](ABOUT.md) |
| Repository entry point | [`README.md`](../README.md) |

---

## Definition of done

InfrGate is resume-worthy when major claims are backed by **code and reproducible tests**, not documentation alone.

Demonstrable capabilities by phase:

| Capability | Phase |
|------------|-------|
| Auth, rate limiting, tenant isolation, usage ledger | 1 |
| Failover, circuit breaker, retries | 2 |
| SSE streaming, worker safety, webhooks | 3 |
| Health-aware adaptive routing | 4 |
| Load tests, metrics, operational runbook | 5 |
