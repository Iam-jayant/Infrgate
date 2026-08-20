# 12 — Observability

> Structured logging, health endpoints, and Prometheus-style metrics for InfrGate.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | Phase 1 (logging), Phase 5 (health endpoints, metrics) |
| **Audience** | All contributors |

---

## 1. Overview

Observability in InfrGate is built on three pillars:

| Pillar | Phase | Implementation |
|---|---|---|
| **Structured logging** | 1 | JSON logs with request correlation |
| **Health endpoints** | 5 | Liveness and readiness probes |
| **Metrics** | 5 | Prometheus text exposition format |

---

## 2. Structured logging `[Phase 1]`

### 2.1 Format

All logs are JSON-formatted for machine parsing:

```json
{
  "timestamp": "2026-01-15T10:30:00.123Z",
  "level": "INFO",
  "logger": "infrgate.gateway",
  "message": "request_completed",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "tenant-uuid",
  "model": "gemini-2.0-flash",
  "provider": "gemini",
  "latency_ms": 450,
  "status_code": 200,
  "prompt_tokens": 25,
  "completion_tokens": 8
}
```

### 2.2 Log levels

| Level | Usage |
|---|---|
| **ERROR** | Unrecoverable failures: circuit breaker trips, dead-letter jobs, DB connection failures |
| **WARNING** | Degraded operation: retries, rate limit degraded (Redis down), spend cap approaching |
| **INFO** | Normal operations: request completed, job processed, worker started/stopped |
| **DEBUG** | Detailed internals: routing scores, provider request/response payloads, cache hits |

### 2.3 Correlation

Every log line within a request lifecycle includes `request_id`. This allows tracing a single request across:

- Authentication
- Rate limiting
- Routing decision
- Provider call (including retries)
- Usage recording

### 2.4 Logger configuration

```python
import structlog

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.LOG_LEVEL, logging.INFO)
    ),
)

# Usage
logger = structlog.get_logger()

# In request middleware
structlog.contextvars.bind_contextvars(
    request_id=request_id,
    tenant_id=str(tenant.id),
)
```

### 2.5 Standard log events

| Event | Level | Fields | When |
|---|---|---|---|
| `request_started` | INFO | `request_id`, `method`, `path`, `tenant_id` | Request received |
| `request_completed` | INFO | `request_id`, `status_code`, `latency_ms`, `model`, `provider` | Response sent |
| `request_error` | ERROR | `request_id`, `error_type`, `message` | Unhandled error |
| `auth_failed` | WARNING | `reason`, `ip` | Authentication failure |
| `rate_limit_exceeded` | WARNING | `tenant_id`, `limit`, `window` | Rate limit hit |
| `rate_limit_degraded` | WARNING | `tenant_id`, `error` | Redis unavailable |
| `provider_call` | INFO | `provider`, `model`, `latency_ms` | Provider call completed |
| `provider_retry` | WARNING | `provider`, `attempt`, `delay_s`, `error` | Retry scheduled |
| `provider_error` | ERROR | `provider`, `status_code`, `error` | Provider error |
| `circuit_opened` | ERROR | `provider` | Circuit breaker tripped |
| `circuit_closed` | INFO | `provider` | Circuit breaker recovered |
| `usage_recorded` | INFO | `request_id`, `tokens`, `cost_cents` | Usage ledger entry |
| `spend_threshold` | WARNING | `tenant_id`, `threshold`, `current_spend` | Spend threshold crossed |
| `job_completed` | INFO | `job_id`, `job_type` | Background job done |
| `job_dead_letter` | ERROR | `job_id`, `job_type`, `error` | Job permanently failed |
| `worker_started` | INFO | — | Worker process started |
| `worker_stopped` | INFO | — | Worker process stopped |

---

## 3. Health endpoints `[Phase 5]`

### 3.1 Liveness probe

**`GET /health/live`** — No authentication required.

Checks: process is running and can handle HTTP requests.

```python
@router.get("/health/live")
async def liveness():
    return {"status": "ok"}
```

**Response:** Always `200 OK` if the process is alive.

**Use case:** Kubernetes liveness probe — restarts the container if the process is stuck.

### 3.2 Readiness probe

**`GET /health/ready`** — No authentication required.

Checks: the gateway can serve requests (critical dependencies available).

```python
@router.get("/health/ready")
async def readiness(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    registry: ProviderRegistry = Depends(get_registry),
):
    checks = {}

    # PostgreSQL
    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "error"

    # Redis
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    # Providers
    checks["providers"] = {}
    for provider_info in registry.list_providers():
        circuit = await get_circuit_state(redis, provider_info.name)
        checks["providers"][provider_info.name] = (
            "healthy" if circuit != CircuitState.OPEN else "unhealthy"
        )

    # Ready if Postgres and Redis are ok
    is_ready = checks["postgres"] == "ok" and checks["redis"] == "ok"
    status_code = 200 if is_ready else 503

    return JSONResponse(
        content={
            "status": "ready" if is_ready else "not_ready",
            "checks": checks,
        },
        status_code=status_code,
    )
```

---

## 4. Prometheus metrics `[Phase 5]`

### 4.1 Metrics catalog

#### Counters

| Metric | Labels | Description |
|---|---|---|
| `infrgate_requests_total` | `tenant`, `model`, `status` | Total inference requests |
| `infrgate_tokens_total` | `tenant`, `type` (`prompt`/`completion`) | Total tokens consumed |
| `infrgate_provider_requests_total` | `provider`, `status` | Provider calls |
| `infrgate_provider_retries_total` | `provider` | Retry attempts |
| `infrgate_provider_timeouts_total` | `provider` | Timeout events |
| `infrgate_circuit_state_changes_total` | `provider`, `transition` | Circuit state transitions |
| `infrgate_failovers_total` | `from_provider`, `to_provider` | Failover events |
| `infrgate_rate_limit_rejections_total` | `tenant` | Rate limit rejections |
| `infrgate_auth_failures_total` | `reason` | Authentication failures |
| `infrgate_jobs_processed_total` | `job_type`, `status` | Worker jobs processed |
| `infrgate_webhooks_delivered_total` | `event_type`, `status` | Webhook deliveries |

#### Histograms

| Metric | Labels | Buckets | Description |
|---|---|---|---|
| `infrgate_request_duration_seconds` | `model` | 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30 | End-to-end request duration |
| `infrgate_provider_latency_seconds` | `provider` | 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30 | Upstream provider latency |
| `infrgate_job_duration_seconds` | `job_type` | 0.1, 0.5, 1, 5, 10, 30, 60 | Background job duration |

#### Gauges

| Metric | Labels | Description |
|---|---|---|
| `infrgate_provider_circuit_state` | `provider` | 0=closed, 1=open, 2=half_open |
| `infrgate_active_requests` | — | Currently in-flight requests |
| `infrgate_jobs_pending` | `job_type` | Pending jobs in queue |

### 4.2 Metrics endpoint

**`GET /metrics`** — No authentication required. Returns Prometheus text exposition format.

```python
from prometheus_client import (
    Counter, Histogram, Gauge,
    generate_latest, CONTENT_TYPE_LATEST,
)

# Define metrics
REQUESTS_TOTAL = Counter(
    "infrgate_requests_total",
    "Total inference requests",
    ["tenant", "model", "status"],
)

REQUEST_DURATION = Histogram(
    "infrgate_request_duration_seconds",
    "End-to-end request duration",
    ["model"],
    buckets=[0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30],
)

# Endpoint
@router.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
```

### 4.3 Metrics collection points

| Collection point | Metrics updated | Phase |
|---|---|---|
| Request middleware | `requests_total`, `request_duration`, `active_requests` | 5 |
| Rate limit check | `rate_limit_rejections_total` | 5 |
| Provider call | `provider_requests_total`, `provider_latency`, `tokens_total` | 5 |
| Retry execution | `provider_retries_total`, `provider_timeouts_total` | 5 |
| Circuit breaker | `circuit_state_changes_total`, `provider_circuit_state` | 5 |
| Failover | `failovers_total` | 5 |
| Worker loop | `jobs_processed_total`, `job_duration`, `jobs_pending` | 5 |
| Webhook delivery | `webhooks_delivered_total` | 5 |

---

## 5. Alerting recommendations

These are suggested alerts for production monitoring:

| Alert | Condition | Severity |
|---|---|---|
| **High error rate** | `rate(infrgate_requests_total{status="error"}[5m]) / rate(infrgate_requests_total[5m]) > 0.05` | Critical |
| **Circuit breaker open** | `infrgate_provider_circuit_state == 1` | Warning |
| **All providers down** | `sum(infrgate_provider_circuit_state == 1) == count(providers)` | Critical |
| **High latency (p99)** | `histogram_quantile(0.99, infrgate_request_duration_seconds) > 10` | Warning |
| **Job queue backing up** | `infrgate_jobs_pending > 100` | Warning |
| **Dead-letter jobs** | `rate(infrgate_jobs_processed_total{status="dead_letter"}[1h]) > 0` | Warning |
| **Rate limit degraded** | Structured log `rate_limit_degraded` events | Warning |

---

## 6. Log aggregation

### 6.1 Docker Compose logging

```yaml
gateway:
  logging:
    driver: "json-file"
    options:
      max-size: "10m"
      max-file: "3"
```

### 6.2 Production path

In production, logs would be shipped to a centralized system (ELK, Loki, CloudWatch). This is operational infrastructure beyond InfrGate's scope, but the JSON-structured logging format is designed for easy ingestion.

---

## References

- [01-system-overview.md](01-system-overview.md) — Request lifecycle (where logs are emitted)
- [02-api-design.md](02-api-design.md) — Health and metrics endpoint contracts
- [08-reliability.md](08-reliability.md) — Circuit breaker metrics
