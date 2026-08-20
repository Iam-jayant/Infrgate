# 13 — Future Work

> Explicit non-goals, deferred capabilities, and rationale for InfrGate.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | — (beyond Phase 5) |
| **Audience** | All contributors, reviewers |

---

## 1. Overview

This document catalogues capabilities that are **explicitly deferred** from InfrGate's five-phase roadmap. Each item includes a rationale for why it is not included and under what conditions it might be reconsidered.

The guiding principle:

> Build the smallest coherent production-style inference gateway first, then progressively add intelligence and sophistication.

---

## 2. Deferred capabilities

### 2.1 Full OpenAI API compatibility

| Item | Details |
|---|---|
| **What** | Supporting the entire OpenAI API surface: embeddings, images, audio, fine-tuning, assistants, file uploads |
| **Why deferred** | InfrGate focuses on chat completions — the highest-value LLM inference pattern. Expanding to the full API surface adds significant complexity without proportional value for the control-plane thesis |
| **Reconsider when** | Chat completions routing is proven and stable; demand for other modalities emerges |

### 2.2 Policy DSL / rules engine

| Item | Details |
|---|---|
| **What** | A configurable rules language for defining routing policies, content filters, request transformations, or custom rate limit rules |
| **Why deferred** | Premature abstraction. The current hard-coded policy checks (plan authorization, spend caps) are sufficient. A DSL adds parser complexity and debugging difficulty before the core patterns are proven |
| **Reconsider when** | Multiple tenants need meaningfully different policy configurations that can't be expressed via plan config |

### 2.3 Kafka / NATS / RabbitMQ

| Item | Details |
|---|---|
| **What** | Replacing the PostgreSQL job queue with a dedicated message broker |
| **Why deferred** | PostgreSQL `FOR UPDATE SKIP LOCKED` is sufficient for InfrGate's job volume (usage aggregation, webhooks). A dedicated broker adds operational overhead (separate cluster, monitoring, operational expertise) |
| **Reconsider when** | Job throughput exceeds what Postgres can handle (thousands of jobs/second); event-driven architectures are needed (pub/sub patterns, event sourcing) |

### 2.4 Machine-learning-based routing

| Item | Details |
|---|---|
| **What** | Using ML models to predict optimal provider routing (e.g., predicting latency, quality, or cost from request features) |
| **Why deferred** | InfrGate uses heuristic routing (EWMA, configurable weights) which is explainable and debuggable. ML routing adds training pipelines, model serving infrastructure, and makes routing decisions opaque |
| **Reconsider when** | The heuristic approach demonstrably underperforms; sufficient historical routing data exists for training |

### 2.5 Shadow routing and request replay

| Item | Details |
|---|---|
| **What** | Duplicating live requests to a secondary provider for comparison (shadow mode), or replaying historical requests for benchmarking |
| **Why deferred** | Shadow routing doubles upstream costs and requires careful comparison logic. Replay needs request storage and determinism guarantees |
| **Reconsider when** | Provider evaluation is critical (e.g., comparing new model quality before cutover) |

### 2.6 Distributed tracing (OpenTelemetry)

| Item | Details |
|---|---|
| **What** | Full OpenTelemetry integration with spans, trace context propagation, and a trace collector (Jaeger, Tempo) |
| **Why deferred** | Structured logging with `request_id` correlation provides sufficient observability for a single-service system. OTel adds SDK dependencies, a collector service, and storage backend |
| **Reconsider when** | InfrGate becomes multi-service; debugging requires cross-service trace correlation |

### 2.7 Admin dashboard / frontend

| Item | Details |
|---|---|
| **What** | A web UI for tenant management, usage visualization, provider health monitoring, and routing configuration |
| **Why deferred** | InfrGate is a **backend** project. The admin API is the interface; building a frontend diverts effort from the core control-plane capabilities |
| **Reconsider when** | Non-technical operators need to manage InfrGate; the API surface is proven and stable |

### 2.8 Premature microservice decomposition

| Item | Details |
|---|---|
| **What** | Splitting InfrGate into separate services (auth service, routing service, usage service, etc.) |
| **Why deferred** | The modular monolith architecture provides clear component boundaries without network overhead, distributed transaction complexity, or service discovery |
| **Reconsider when** | Different components need independent scaling or deployment cycles; team size requires code ownership boundaries |

### 2.9 Multi-region deployment

| Item | Details |
|---|---|
| **What** | Deploying InfrGate across multiple geographic regions with request routing based on client location |
| **Why deferred** | Adds DNS-based routing, cross-region database replication, and consistency challenges. Single-region is sufficient for the current scope |
| **Reconsider when** | Latency-sensitive clients in multiple regions; regulatory requirements for data residency |

### 2.10 Request caching

| Item | Details |
|---|---|
| **What** | Caching identical requests to avoid redundant provider calls. Semantic caching (similar-enough requests) |
| **Why deferred** | LLM responses are often non-deterministic (temperature > 0). Cache invalidation for semantic similarity is an unsolved problem. Caching adds complexity for marginal cost savings |
| **Reconsider when** | Deterministic use cases emerge (embeddings, fixed prompts); cost savings justify cache infrastructure |

### 2.11 Request/response transformations

| Item | Details |
|---|---|
| **What** | Middleware for transforming requests before sending to providers (e.g., prompt injection, content filtering, PII redaction) |
| **Why deferred** | Transformation logic is application-specific. InfrGate is a routing and reliability layer, not a prompt engineering tool |
| **Reconsider when** | Common transformation patterns emerge across multiple tenants |

### 2.12 API versioning

| Item | Details |
|---|---|
| **What** | Supporting multiple API versions simultaneously (`/v1/`, `/v2/`) with migration guides |
| **Why deferred** | InfrGate currently has one API version. Versioning adds router complexity and compatibility testing surface |
| **Reconsider when** | Breaking changes to the API are necessary and existing clients cannot migrate immediately |

---

## 3. Summary table

| Capability | Complexity | Value (current) | Status |
|---|---|---|---|
| Full OpenAI API | High | Low | Deferred |
| Policy DSL | Medium | Low | Deferred |
| Kafka/NATS/RabbitMQ | Medium | Low | Deferred |
| ML-based routing | High | Low | Deferred |
| Shadow routing | Medium | Medium | Deferred |
| OpenTelemetry | Medium | Low | Deferred |
| Admin dashboard | High | Medium | Deferred |
| Microservice split | High | Low | Deferred |
| Multi-region | High | Low | Deferred |
| Request caching | Medium | Low | Deferred |
| Request transforms | Medium | Low | Deferred |
| API versioning | Low | Low | Deferred |

---

## 4. Decision framework

When evaluating whether to pull a deferred capability into scope:

1. **Does it strengthen the core thesis?** InfrGate is a routing, reliability, and accounting control plane. If the feature doesn't make routing smarter, reliability stronger, or accounting more precise, it's noise.

2. **Can it be tested and demonstrated?** Every phase must produce testable, demonstrable capabilities. Features that can't be evidenced with code and tests don't belong in the roadmap.

3. **Does it add operational burden?** New infrastructure (brokers, trace collectors, ML pipelines) must justify their operational cost against the marginal capability improvement.

4. **Is the simpler alternative exhausted?** Before adding a DSL, prove that config files are insufficient. Before adding OTel, prove that structured logs are insufficient. Complexity is easy to add and hard to remove.

---

## References

- [About.md](../../About.md) — Project thesis and phase breakdown
- [01-system-overview.md](01-system-overview.md) — Current architecture
