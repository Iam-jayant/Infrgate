# InfrGate

InfrGate is a scalable, reliable API gateway and proxy for Large Language Models (LLMs).

## What this is

Applications integrating multiple LLM providers repeatedly solve the same infrastructure problems: API authentication and tenant isolation, provider selection and routing, failover, rate limits and spend caps, and usage accounting. 

InfrGate centralizes those concerns behind a single OpenAI-compatible backend gateway. Client applications simply point their OpenAI SDKs to the InfrGate URL instead of \pi.openai.com\, and gain automatic failover, cost controls, and tenant isolation. Instead of building auth, quotas, failover, and billing logic in every product, applications simply point their SDKs to the InfrGate URL. It is a production-minded control plane where reliability, routing, tenant isolation, and usage accounting are first-class concerns.

## Architecture

```text
HTTP request -> InfrGate
                 |
      Authentication & Tenant Isolation
                 |
      Rate Limiting & Policy Checks (Spend Caps)
                 |
           Routing Engine
                 |
Provider Execution (Timeout, Retry, Circuit Breaker, Failover)
                 |
         Usage Persistence
                 |
 Background Processing (Webhooks, Aggregation)
```

The gateway architecture separates the inference hot path from durable background tasks. The **Gateway API** handles HTTP interfaces, tenant context, rate limiting, and provider orchestration. The **Background Worker** aggregates usage and safely delivers webhooks using a PostgreSQL `FOR UPDATE SKIP LOCKED` queue, ensuring long-running operations never block live inferences.

## Current State & Implementation

InfrGate is currently fully through **Phase 5 (Hardening & Production Evidence)**.

**Phase 1 - Foundation**
- Exposes an OpenAI-compatible `POST /v1/chat/completions` endpoint.
- Enforces strict tenant isolation, API-key authentication, and usage accounting.
- Provides a durable usage ledger with idempotent request tracking.
- Operates primarily over Google Gemini (adapter implemented).

**Phase 2 - Provider Abstraction + Reliability**
- Employs priority-based routing across multiple models and providers.
- Hardened with sliding-window circuit breakers (CLOSED -> OPEN -> HALF_OPEN).
- Executes automatic failover, timeouts, and exponential backoff retries.

**Phase 3 - Streaming + Async Processing**
- Fully supports streaming via Server-Sent Events (SSE).
- Handles mid-stream client disconnects with partial usage recording.
- Employs a PostgreSQL-backed worker queue (`SKIP LOCKED`) for async tasks.
- Asynchronously delivers spend-threshold alert webhooks.

**Phase 4 - Intelligent Routing**
- Tracks rolling error/latency signals and EWMA-based health score calculations.
- Implements configurable, cost-aware heuristic routing driven by empirical provider health.

**Phase 5 - Hardening & Production Evidence**
- Secured endpoints, hardened exception handlers, and comprehensive migration safety.
- Exposes Prometheus-style `/metrics`, `/health/live`, and `/health/ready` endpoints.
- Validated via rigorous failure-injection, integration suites, and load testing.

## Engineering Challenges

### Claim-First Idempotency and CAS Races
Network retries often cause double-billing. The naive approach to idempotency (checking for a past request and then running the new one) fails under concurrency. InfrGate uses a claim-first model with tenant-scoped Composite Unique Constraints (`UNIQUE(tenant_id, idempotency_key)`). Before calling a provider, the gateway inserts a `pending` ledger row. If a concurrent duplicate arrives, the DB constraint safely catches it, and a Compare-and-Swap (CAS) update is used to safely reclaim abandoned leases.

### Orphaned Pending Rows and Lease-Expiry Recovery
If the server crashes mid-flight, a `pending` idempotency row remains stuck indefinitely, rejecting all client retries. Instead of building complex distributed locks, InfrGate uses a 90-second lease window. When a duplicate request arrives, if the existing `pending` row is older than 90 seconds, the new request reclaims the row using a CAS update (`UPDATE ... WHERE claimed_at = ?`), effectively self-healing without manual intervention.

### Asyncio Cancellation as a BaseException
During mid-flight provider execution, if a client disconnected or the server timed out, the resulting `asyncio.CancelledError` would skip standard `except Exception:` blocks (since Python 3.8, it inherits directly from `BaseException`). This caused rows to be orphaned. By catching `asyncio.CancelledError` explicitly and wrapping cleanup blocks in `anyio.CancelScope(shield=True)`, the gateway guarantees the usage ledger is always transitioned to a `partial` or `failed` state.

### Webhook SSRF and DNS Rebinding
Dispatching webhooks dynamically opens vectors for Server-Side Request Forgery (SSRF) and DNS rebinding attacks. Validating the URL via standard parsing is insufficient. The webhook dispatcher implements strict IP-level verification: resolving the host asynchronously, explicitly blocking internal blocks (`10.0.0.0/8`, `127.0.0.0/8`, `169.254.169.254`), and enforcing HTTPS with TLS verification on all external traffic.

## Known Limitations

- **Spend-Cap Enforcement:** Spend caps are enforced as a best-effort pre-check. Because usage is reconciled after the provider call, extremely high-concurrency bursts near the limit could theoretically exceed the cap by a small margin before enforcement catches up.
- **Narrow Claim-Insert Window:** If a cancellation lands exactly during the initial `claim_or_replay_request` database insert, it may bypass the shielded `try` block. This is a known acceptable race condition bounded by the 90-second lease-expiry self-heal mechanism.

## Tech Stack

| Component | Choice | Why |
| :--- | :--- | :--- |
| **Language** | Python 3.12 | Async-first ecosystem, dominant in AI/ML stacks. |
| **Web Framework** | FastAPI | High-performance ASGI, native Pydantic validation. |
| **Database** | PostgreSQL | System of record for strict relational guarantees. |
| **Message Queue**| Postgres `SKIP LOCKED` | Removes the need for Kafka/RabbitMQ while remaining transactionally sound. |
| **Cache/State** | Redis | Fast ephemeral state for rate limits and circuit breakers. |
| **ORM/Migrations** | SQLAlchemy / Alembic | Mature async database modeling and schema evolution. |

## Getting Started

1. **Install dependencies:**
   ```bash
   poetry install
   ```
2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Add your GEMINI_API_KEY to test out the integration
   ```
3. **Start infrastructure (Postgres, Redis):**
   ```bash
   docker compose up -d
   ```
4. **Run migrations:**
   ```bash
   poetry run alembic upgrade head
   ```
5. **Start Gateway and Worker:**
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

**Note:** The test suite uses an in-memory `aiosqlite` database and heavily mocked Redis instances (`AsyncMock`) to guarantee execution speed and isolation, deliberately avoiding the overhead of `testcontainers`.

## API Reference

**POST `/v1/chat/completions`**

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-test-123" \
  -H "Idempotency-Key: my-unique-key-456" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Hello, world!"}],
    "stream": false
  }'
```

**Standardized Error Envelope:**
```json
{
  "error": {
    "message": "Spend cap exceeded",
    "code": 403,
    "type": "error"
  }
}
```

## Documentation

- **[Project Overview & Architecture](ABOUT.md)**
- **[Engineering Specifications](supporting_docs/spec/)** — Detailed specifications for core sub-systems (idempotency, routing, stream accounting, etc.)
- **[Spec Index](supporting_docs/README.md)** — Full list of technical docs.

## Project Structure

```text
src/infrgate/
|-- api/          # FastAPI endpoints, admin routing, and health checks
|-- auth/         # Tenant API key validation and policy enforcement
|-- db/           # SQLAlchemy async engine and ORM models
|-- middleware/   # ASGI middlewares for metrics and request IDs
|-- providers/    # External LLM adapters and routing registry
|-- schemas/      # Pydantic models for validation and responses
|-- services/     # Core logic (idempotency, circuit breaker, usage, routing)
|-- worker/       # Postgres-backed async background job loop
```

## Roadmap

The current iteration completes **Phase 5**. The foundational features, provider abstractions, asynchronous streaming, dynamic routing, and production hardening are complete. 

Deferred future priorities include:
- Native OpenTelemetry distributed tracing
- Advanced machine-learning-based routing
- Broader OpenAI parity (Embeddings, Function Calling)
- Shadow routing and replay testing

## License

MIT
