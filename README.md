# InfrGate

**InfrGate** is a scalable, reliable API gateway and proxy for Large Language Models (LLMs) featuring robust tenant isolation, rate limiting, and observability.

---

## 🌐 Live Demo

- **Frontend UI (Vercel):** [Insert Vercel Link Here]
- **Backend Gateway (Cloud Run):** [Insert Cloud Run Link Here]

---

## What this is

Applications integrating multiple LLM providers repeatedly solve the same infrastructure problems: API authentication and tenant isolation, provider selection and routing, failover, rate limits and spend caps, and usage accounting. 

InfrGate centralizes those concerns behind a single OpenAI-compatible backend gateway. Client applications simply point their OpenAI SDKs to the InfrGate URL instead of `api.openai.com`, and gain automatic failover, cost controls, and tenant isolation. 

## 🏗️ Production Architecture

This project is built to scale out-of-the-box utilizing serverless & managed infrastructure:

- **Frontend Application:** Hosted on **Vercel** for global CDN edge delivery and fast static asset loading. Features a minimalist, realistic aesthetic inspired by Claude's interface.
- **Backend API (Gateway & Worker):** Hosted on **Google Cloud Run**, providing stateless, auto-scaling containerized compute that scales to zero and handles massive concurrent traffic bursts.
- **Primary Database:** **Supabase** (Managed PostgreSQL) acts as the system of record. It securely stores tenant API keys, model configurations, and the durable token usage ledger.
- **State & Rate Limiting:** **Upstash** (Serverless Redis) provides sub-millisecond ephemeral state for Token Bucket rate limiting and sliding-window circuit breakers.
- **Model Providers:** Seamlessly multiplexes between **Hugging Face** (via `router.huggingface.co`), **Google Gemini**, and **OpenAI**.

## Flow Architecture

```text
HTTP request -> Vercel UI -> Google Cloud Run (InfrGate)
                                 |
                      Authentication & Tenant Isolation (Supabase)
                                 |
                 Rate Limiting & Policy Checks (Upstash Redis)
                                 |
                           Routing Engine
                                 |
           Provider Execution (Timeout, Retry, Failover)
                                 |
                         Usage Persistence
                                 |
         Background Processing (SKIP LOCKED Queue on Supabase)
```

## Current State & Implementation

InfrGate is currently fully through **Phase 5 (Hardening & Production Evidence)**.

**Phase 1 - Foundation**
- Exposes an OpenAI-compatible `POST /v1/chat/completions` endpoint.
- Enforces strict tenant isolation, API-key authentication, and usage accounting.
- Provides a durable usage ledger with idempotent request tracking.

**Phase 2 - Provider Abstraction + Reliability**
- Employs priority-based routing across multiple models and providers (OpenAI, Gemini, Hugging Face).
- Hardened with sliding-window circuit breakers (CLOSED -> OPEN -> HALF_OPEN).
- Executes automatic failover, timeouts, and exponential backoff retries.

**Phase 3 - Streaming + Async Processing**
- Fully supports streaming via Server-Sent Events (SSE).
- Handles mid-stream client disconnects with partial usage recording.
- Employs a PostgreSQL-backed worker queue (`SKIP LOCKED`) for async tasks.

**Phase 4 - Intelligent Routing**
- Tracks rolling error/latency signals and EWMA-based health score calculations.
- Implements configurable, cost-aware heuristic routing driven by empirical provider health.

## Engineering Challenges

### Claim-First Idempotency and CAS Races
Network retries often cause double-billing. The naive approach to idempotency fails under concurrency. InfrGate uses a claim-first model with tenant-scoped Composite Unique Constraints (`UNIQUE(tenant_id, idempotency_key)`). Before calling a provider, the gateway inserts a `pending` ledger row. If a concurrent duplicate arrives, a Compare-and-Swap (CAS) update reclaims abandoned leases using a 90-second lease window.

### Asyncio Cancellation as a BaseException
During mid-flight provider execution, if a client disconnected or the server timed out, the resulting `asyncio.CancelledError` would skip standard exception blocks. By catching it explicitly and wrapping cleanup blocks in `anyio.CancelScope(shield=True)`, the gateway guarantees the usage ledger is always transitioned to a `partial` or `failed` state.

## Tech Stack

| Component | Choice | Why |
| :--- | :--- | :--- |
| **Language** | Python 3.12 | Async-first ecosystem, dominant in AI/ML stacks. |
| **Web Framework** | FastAPI | High-performance ASGI, native Pydantic validation. |
| **Database** | PostgreSQL (Supabase) | System of record for strict relational guarantees. |
| **Message Queue**| Postgres `SKIP LOCKED` | Removes the need for Kafka/RabbitMQ while remaining transactionally sound. |
| **Cache/State** | Redis (Upstash) | Fast ephemeral state for rate limits and circuit breakers. |
| **Hosting** | Cloud Run & Vercel | Infinite serverless scale with zero-downtime deployments. |

## Getting Started (Local Development)

1. **Install dependencies:**
   ```bash
   poetry install
   ```
2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Add your HUGGINGFACE_API_KEY, Supabase POSTGRES_URL, and Upstash REDIS_URL
   ```
3. **Run migrations:**
   ```bash
   poetry run alembic upgrade head
   ```
4. **Start Gateway and Worker:**
   ```bash
   poetry run infrgate
   poetry run python -m infrgate.worker.cli
   ```

## Testing

The project maintains **82 passing tests** across unit, integration, and load suites.

To run the test suite:
```bash
poetry run pytest -v
```

## API Reference

**POST `/v1/chat/completions`**

```bash
curl -X POST https://[YOUR_CLOUD_RUN_URL]/v1/chat/completions \
  -H "Authorization: Bearer [YOUR_TENANT_API_KEY]" \
  -H "Idempotency-Key: my-unique-key-456" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen2.5-72B-Instruct",
    "messages": [{"role": "user", "content": "Hello, world!"}],
    "stream": true
  }'
```
