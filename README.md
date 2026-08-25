<div align="center">
  <h1>InfrGate</h1>
  <p><strong>Intelligent inference control plane for Large Language Models.</strong></p>
  
  [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
  [![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)](https://fastapi.tiangolo.com)
  [![Next.js](https://img.shields.io/badge/Next.js-000000?style=flat&logo=next.js&logoColor=white)](https://nextjs.org/)
  
  [Website & Demo](http://infrgate.vercel.app/) • [Documentation](#architecture) • [API Reference](#api-reference)
</div>

<br />

**InfrGate** is a scalable, reliable API gateway and proxy for Large Language Models (LLMs) featuring robust tenant isolation, rate limiting, and observability. It acts as a drop-in replacement for OpenAI's SDK, instantly upgrading your application with enterprise-grade infrastructure.

---

## Live Environments

- **Landing Page & Overview:** [http://infrgate.vercel.app/](http://infrgate.vercel.app/)
- **Live Gateway API (Backend):** `https://infrgate.onrender.com/v1/chat/completions`
- **Demo Tenant API Key:** `365c7a7b.ZZVujqiq-gWiHWJWAcqxz8x8QrwiRi4rWOFr5DMVr1I`

---

## The Problem & Solution

Applications integrating multiple LLM providers repeatedly solve the same infrastructure problems: API authentication and tenant isolation, provider selection and routing, failover, rate limits and spend caps, and usage accounting. 

**InfrGate** centralizes those concerns behind a single OpenAI-compatible backend gateway. Client applications simply point their OpenAI SDKs to the InfrGate URL instead of `api.openai.com`, and instantly gain automatic failover, cost controls, and tenant isolation. 

## Architecture

This project is built to scale out-of-the-box utilizing serverless & managed infrastructure:

- **Frontend Application:** A highly interactive, dark brutalist React application built with **Next.js** and deployed on **Vercel**.
- **Unified Gateway (API):** Hosted on **Render** as a high-performance containerized Docker service built on **FastAPI**.
- **Primary Database:** **Supabase** (Managed PostgreSQL) acts as the system of record. It securely stores tenant API keys, model configurations, and the durable token usage ledger.
- **State & Rate Limiting:** **Upstash** (Serverless Redis) provides sub-millisecond ephemeral state for Token Bucket rate limiting and sliding-window circuit breakers.
- **Provider Multiplexing:** Seamlessly routes between **Hugging Face** (via `router.huggingface.co`), **Google Gemini**, and **OpenAI**.

### Flow Architecture

```mermaid
graph TD
    Client["Client Request (OpenAI SDK)"] --> API["InfrGate Gateway (FastAPI)"]
    API --> Auth["Authentication & Tenant Isolation (Supabase)"]
    Auth --> RateLimit["Rate Limiting & Policy Check (Upstash Redis)"]
    RateLimit --> Router["Intelligent Routing Engine"]
    Router --> Execute["Provider Execution (Timeout, Retry, Failover)"]
    Execute --> Persist["Idempotent Usage Persistence"]
    Persist --> Worker["Background Processing (SKIP LOCKED Queue on Postgres)"]
    
    style Client fill:#141414,stroke:#333,stroke-width:2px,color:#fff
    style API fill:#ff5500,stroke:#333,stroke-width:2px,color:#fff
    style Router fill:#1e1e1e,stroke:#333,stroke-width:2px,color:#fff
```

## Engineering Highlights

### Claim-First Idempotency and CAS Races
Network retries often cause double-billing. The naive approach to idempotency fails under concurrency. InfrGate uses a claim-first model with tenant-scoped Composite Unique Constraints (`UNIQUE(tenant_id, idempotency_key)`). Before calling a provider, the gateway inserts a `pending` ledger row. If a concurrent duplicate arrives, a Compare-and-Swap (CAS) update reclaims abandoned leases using a 90-second lease window.

### Resilient Async Cancellation
During mid-flight provider execution, if a client disconnected or the server timed out, the resulting `asyncio.CancelledError` would skip standard exception blocks. By catching it explicitly and wrapping cleanup blocks in `anyio.CancelScope(shield=True)`, the gateway guarantees the usage ledger is always transitioned to a `partial` or `failed` state.

### Sliding-Window Circuit Breakers
Hardened with intelligent state transitions (`CLOSED` -> `OPEN` -> `HALF_OPEN`), ensuring that degraded upstream providers do not cause cascading failures within the gateway.

## Tech Stack

| Component | Technology | Rationale |
| :--- | :--- | :--- |
| **Frontend** | Next.js, React, Tailwind | Best-in-class developer experience for interactive web apps. |
| **Backend/Gateway** | Python 3.12, FastAPI | Async-first ecosystem, dominant in AI/ML stacks with native validation. |
| **Database** | PostgreSQL (Supabase) | System of record for strict relational guarantees. |
| **Message Queue**| Postgres `SKIP LOCKED` | Removes the need for Kafka/RabbitMQ while remaining transactionally sound. |
| **Cache/State** | Redis (Upstash) | Fast ephemeral state for rate limits and circuit breakers. |

## Getting Started (Local Development)

### 1. Gateway & Backend

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

### 2. Frontend Web App

```bash
cd Frontend
pnpm install
pnpm run dev
```

## Testing

The project maintains **82 passing tests** across unit, integration, and load suites.

To run the test suite:
```bash
poetry run pytest -v
```

## API Reference

**POST `/v1/chat/completions`**

Use your standard OpenAI SDK or send a raw cURL request:

```bash
curl -X POST https://infrgate.onrender.com/v1/chat/completions \
  -H "Authorization: Bearer 365c7a7b.ZZVujqiq-gWiHWJWAcqxz8x8QrwiRi4rWOFr5DMVr1I" \
  -H "Idempotency-Key: req-$(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen2.5-72B-Instruct",
    "messages": [{"role": "user", "content": "Explain quantum computing in one sentence."}],
    "stream": true
  }'
```

---
<div align="center">
  <sub>Built by the InfrGate Team.</sub>
</div>
