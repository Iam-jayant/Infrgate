# InfrGate — Engineering Specifications

> Canonical design documentation for InfrGate — the intelligent inference control plane.

This directory contains the complete engineering specification for InfrGate across all five development phases. Every document is **phase-tagged**: sections indicate which phase introduces each capability so you know what's relevant during each development cycle.

---

## Reading guide

| If you are… | Start with |
|---|---|
| New to the project | [`01-system-overview.md`](spec/01-system-overview.md), then [About.md](../About.md) |
| Building Phase 1 | `01` → `02` → `03` → `04` → `05` → `06` → `10` |
| Building Phase 2 | `06` → `07` → `08` |
| Building Phase 3 | `09` → `11` |
| Building Phase 4 | `07` (routing section) |
| Building Phase 5 | `12` → `13` |
| Reviewing the API | [`02-api-design.md`](spec/02-api-design.md) |
| Understanding the data model | [`03-data-model.md`](spec/03-data-model.md) |

---

## Specification index

| # | Document | Scope | Primary phase | Status |
|---|---|---|---|---|
| 01 | [System Overview](spec/01-system-overview.md) | HLD, component boundaries, deployment model | All | ✅ Final |
| 02 | [API Design](spec/02-api-design.md) | Full API contract, request/response schemas, error model | All | ✅ Final |
| 03 | [Data Model](spec/03-data-model.md) | PostgreSQL schema, indexes, migrations, Redis key design | All | ✅ Final |
| 04 | [Authentication & Tenancy](spec/04-authentication-tenancy.md) | API key lifecycle, auth flow, tenant isolation, plan model | Phase 1 | ✅ Final |
| 05 | [Rate Limiting](spec/05-rate-limiting.md) | Sliding window algorithm, Redis design, rate limit headers | Phase 1 | ✅ Final |
| 06 | [Provider Adapters](spec/06-provider-adapters.md) | Provider interface, Gemini/OpenAI adapters, registry | Phase 1–2 | ✅ Final |
| 07 | [Routing Engine](spec/07-routing-engine.md) | Model resolution, priority routing, health-aware routing | Phase 2–4 | ✅ Final |
| 08 | [Reliability](spec/08-reliability.md) | Timeout, retry, circuit breaker, failover | Phase 2 | ✅ Final |
| 09 | [Streaming](spec/09-streaming.md) | SSE protocol, disconnect handling, partial usage | Phase 3 | ✅ Final |
| 10 | [Usage Accounting](spec/10-usage-accounting.md) | Usage ledger, idempotency, token counting, spend caps | Phase 1–3 | ✅ Final |
| 11 | [Background Worker](spec/11-background-worker.md) | Postgres job queue, webhook delivery, dead-letter | Phase 3 | ✅ Final |
| 12 | [Observability](spec/12-observability.md) | Structured logging, health endpoints, metrics | Phase 1–5 | ✅ Final |
| 13 | [Future Work](spec/13-future-work.md) | Deferred capabilities and rationale | — | ✅ Final |

---

## Conventions

### Phase tags

Every section and table row uses phase annotations:

- **`[Phase 1]`** — included in Phase 1 (Foundation)
- **`[Phase 2]`** — included in Phase 2 (Provider Abstraction + Reliability)
- **`[Phase 3]`** — included in Phase 3 (Streaming + Async Processing)
- **`[Phase 4]`** — included in Phase 4 (Intelligent Routing)
- **`[Phase 5]`** — included in Phase 5 (Hardening & Production Evidence)

### Schema notation

- SQL schemas use PostgreSQL dialect
- Column types are Postgres-native (`UUID`, `TIMESTAMPTZ`, `BIGINT`, etc.)
- Pydantic models use Python type hints
- Redis keys use colon-delimited namespaces: `infrgate:{domain}:{tenant_id}:{key}`

### API notation

- Endpoints follow REST conventions
- Request/response bodies are JSON
- All timestamps are ISO 8601 / UTC
- UUIDs are v4

### Cross-references

Documents link to each other using relative paths. References to project-level docs use `../` prefix.

---

## Source of truth

These specifications are the **design contract**. Implementation must conform to these specs. If implementation diverges, update the spec first (with rationale), then the code.

| Artifact | Role |
|---|---|
| `supporting_docs/spec/` | Design contract (this directory) |
| `About.md` | Project overview and phase breakdown |
| `README.md` | Repository entry point |
| Source code | Implementation of the spec |
| Tests | Executable verification of the spec |
