# InfrGate

**Current version: Phase 1 — Foundation** (in development)

InfrGate is an intelligent inference control plane for routing, reliability, usage metering, and policy enforcement across multiple LLM providers. Client applications send **OpenAI-compatible** requests to InfrGate; the gateway handles authentication, tenant policy, rate limits, provider routing, reliability, and usage accounting.

That client API does **not** require OpenAI. Phase 1 uses **Google Gemini** as the upstream provider so you can run locally with a **free API key** from [Google AI Studio](https://aistudio.google.com/apikey). OpenAI is a Phase 2 adapter for multi-provider failover — optional if you never add a paid key.

```text
Client  (OpenAI-compatible /v1/chat/completions)
  │
  ▼
InfrGate
  ├── Authentication / Tenancy
  ├── Quotas / Rate Limits
  ├── Model & Provider Routing      ← Phase 2+
  ├── Reliability / Failover        ← Phase 2+
  ├── Streaming                     ← Phase 3+
  ├── Usage Ledger
  └── Background Processing         ← Phase 3+
        │
        ├── Gemini                  ← Phase 1 (free API key)
        ├── OpenAI                  ← Phase 2 (optional)
        └── Additional Providers
```

---

## Current status

| | |
|---|---|
| **Project** | InfrGate — full 5-phase inference control plane |
| **Current phase** | **Phase 1 — Foundation** |
| **Phase 1 status** | In development |
| **Apply-ready target** | Phase 1 + Phase 2 complete |

Phase 1 delivers the smallest usable gateway: authenticated non-streaming chat completions via **Gemini**, tenant isolation, rate limiting, spend-cap checks, and a durable usage ledger. See [Project_docs/ABOUT.md](Project_docs/ABOUT.md) for the full project scope, architecture, and phase breakdown.

---

## Total project (all phases)

InfrGate is built incrementally across five phases:

| Phase | Name | Summary |
|-------|------|---------|
| **1** | **Foundation** | Auth, tenants, rate limits, usage ledger, **Gemini** adapter, Docker Compose *(current)* |
| 2 | Provider Abstraction + Reliability | OpenAI + multi-provider routing, timeout, retry, circuit breaker, failover |
| 3 | Streaming + Async Processing | SSE streaming, workers, webhooks, job queue |
| 4 | Intelligent Routing | EWMA health scoring, cost-aware heuristic routing |
| 5 | Hardening & Production Evidence | Load tests, metrics, health endpoints, runbook |

```text
Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5
(current)
```

---

## Tech stack

- **Python 3.12+** · **FastAPI** · **Uvicorn**
- **PostgreSQL** (system of record) · **Redis** (rate limits, ephemeral state)
- **Alembic** · **Pydantic v2** · **httpx** · **pytest**
- **Docker** · **Docker Compose**

---

## What Phase 1 includes

- FastAPI modular application structure
- PostgreSQL migrations: `tenants`, `api_keys`, `usage_ledger`
- API-key authentication and tenant context
- Plan configuration and spend-cap enforcement
- Request correlation via `X-Request-ID`
- One provider adapter: **Gemini** (`GEMINI_API_KEY` from Google AI Studio — free)
- `POST /v1/chat/completions` (non-streaming, OpenAI-compatible client contract)
- Redis-backed rate limiting
- Idempotent usage recording (`UNIQUE(request_id)`)
- Docker Compose local environment
- Unit and integration test foundation

## What Phase 1 does not include yet

- OpenAI adapter and multi-provider failover
- Circuit breaker and retries
- SSE streaming
- Background workers and webhooks
- Adaptive routing and health scoring
- Prometheus metrics and load tests

---

## Documentation

- **[Project overview, architecture, and phases](Project_docs/ABOUT.md)** — start here
- **[Engineering specifications](supporting_docs/spec/)** — detailed spec contract
- **[Spec index](supporting_docs/README.md)** — full spec document list

---

## Quick start

> Phase 1 implementation in progress. Run instructions will be added as the foundation is built.

1. Create a free Gemini key at [Google AI Studio](https://aistudio.google.com/apikey).
2. Set `GEMINI_API_KEY` in your local env (OpenAI is not required for Phase 1).
3. Start the stack:

```bash
# Coming in Phase 1
docker compose up
```

---

## License

TBD
