# 11 — Background Worker

> Postgres job queue, webhook delivery, dead-letter behavior, and worker lifecycle for InfrGate.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | Phase 3 |
| **Audience** | All contributors |

---

## 1. Overview

The background worker processes asynchronous jobs that must not block the inference hot path. It runs as a **separate process** from the gateway, consuming jobs from a PostgreSQL-backed queue.

### Why not Celery / RabbitMQ / Redis queues?

| Alternative | Rejected because |
|---|---|
| Celery + RabbitMQ | Adds operational complexity (broker, result backend); overkill for the job types InfrGate needs |
| Redis-based queue | Redis is ephemeral — jobs could be lost on restart |
| Kafka | Enterprise-grade message bus; massive overkill for this system |

**Choice:** PostgreSQL `FOR UPDATE SKIP LOCKED` — the database is already present, provides ACID guarantees, and the concurrency pattern is well-proven.

---

## 2. Job queue design

### 2.1 Queue table

See [03-data-model.md](03-data-model.md#jobs) for the full `jobs` table DDL.

### 2.2 Job types

| Job type | Phase | Purpose | Payload |
|---|---|---|---|
| `usage_aggregation` | 3 | Aggregate raw usage into summaries | `{ "tenant_id": "uuid", "period": "2026-01" }` |
| `webhook_delivery` | 3 | Deliver a webhook to a tenant endpoint | `{ "tenant_id": "uuid", "event_type": "spend_threshold", "url": "https://...", "body": {...} }` |
| `spend_alert` | 3 | Trigger spend threshold alerts | `{ "tenant_id": "uuid", "threshold": 0.80, "current_spend_cents": 8000 }` |

### 2.3 Job lifecycle

```text
                ┌─────────┐
                │ pending │   ← Job created
                └────┬────┘
                     │
            Worker dequeues
          (FOR UPDATE SKIP LOCKED)
                     │
                ┌────▼────┐
                │ running │   ← attempts += 1
                └────┬────┘
                     │
             ┌───────┼───────┐
             │               │
         Success          Failure
             │               │
        ┌────▼─────┐    ┌───▼──────────┐
        │completed │    │ attempts <   │
        └──────────┘    │ max_attempts?│
                        └───┬──────┬───┘
                            │      │
                           Yes     No
                            │      │
                    ┌───────▼──┐  ┌▼───────────┐
                    │ pending  │  │ dead_letter │
                    │ (retry)  │  │             │
                    └──────────┘  └─────────────┘
```

---

## 3. Dequeue pattern

### 3.1 Atomic dequeue with `FOR UPDATE SKIP LOCKED`

```sql
-- Atomic: claim the next available job
UPDATE jobs
SET    status = 'running',
       started_at = now(),
       attempts = attempts + 1
WHERE  id = (
    SELECT id
    FROM   jobs
    WHERE  status = 'pending'
      AND  scheduled_at <= now()
    ORDER  BY scheduled_at ASC
    LIMIT  1
    FOR UPDATE SKIP LOCKED          -- Skip jobs being claimed by other workers
)
RETURNING *;
```

**Why `SKIP LOCKED`:**
- Multiple workers can run concurrently
- Each worker claims a different job
- No contention or blocking between workers
- No distributed locks needed

### 3.2 Python implementation

```python
async def dequeue_job(db: AsyncSession) -> Job | None:
    """Claim the next pending job. Returns None if no jobs available."""
    result = await db.execute(
        text("""
            UPDATE jobs
            SET    status = 'running',
                   started_at = now(),
                   attempts = attempts + 1
            WHERE  id = (
                SELECT id FROM jobs
                WHERE  status = 'pending'
                  AND  scheduled_at <= now()
                ORDER  BY scheduled_at ASC
                LIMIT  1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
        """)
    )
    row = result.fetchone()
    if row:
        await db.commit()
        return Job.from_row(row)
    return None
```

---

## 4. Job completion

### 4.1 Success

```python
async def complete_job(db: AsyncSession, job_id: str) -> None:
    await db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status="completed", completed_at=func.now())
    )
    await db.commit()
```

### 4.2 Failure with retry

```python
async def fail_job(db: AsyncSession, job: Job, error: str) -> None:
    if job.attempts >= job.max_attempts:
        # Move to dead letter
        await db.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(
                status="dead_letter",
                error_message=error,
                completed_at=func.now(),
            )
        )
        logger.error("job_dead_letter", job_id=str(job.id), error=error)
    else:
        # Schedule retry with exponential backoff
        retry_delay = min(300, 30 * (2 ** (job.attempts - 1)))  # 30s, 60s, 120s, 240s, 300s cap
        await db.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(
                status="pending",
                error_message=error,
                scheduled_at=func.now() + timedelta(seconds=retry_delay),
            )
        )
        logger.warning(
            "job_retry_scheduled",
            job_id=str(job.id),
            attempt=job.attempts,
            retry_in_seconds=retry_delay,
        )
    await db.commit()
```

---

## 5. Worker loop

### 5.1 Main loop

```python
async def worker_main():
    """Main worker loop. Polls for jobs and processes them."""
    logger.info("worker_started")

    while not shutdown_event.is_set():
        try:
            async with get_session() as db:
                job = await dequeue_job(db)

            if job is None:
                # No jobs available — wait before polling again
                await asyncio.sleep(POLL_INTERVAL_S)  # Default: 1 second
                continue

            logger.info("job_processing", job_id=str(job.id), job_type=job.job_type)

            try:
                await process_job(job)
                await complete_job(db, job.id)
                logger.info("job_completed", job_id=str(job.id))
            except Exception as e:
                await fail_job(db, job, str(e))

        except Exception as e:
            logger.error("worker_error", error=str(e))
            await asyncio.sleep(5)  # Back off on unexpected errors

    logger.info("worker_stopped")
```

### 5.2 Job dispatcher

```python
JOB_HANDLERS = {
    "usage_aggregation": handle_usage_aggregation,
    "webhook_delivery": handle_webhook_delivery,
    "spend_alert": handle_spend_alert,
}

async def process_job(job: Job) -> None:
    handler = JOB_HANDLERS.get(job.job_type)
    if not handler:
        raise ValueError(f"Unknown job type: {job.job_type}")
    await handler(job.payload)
```

---

## 6. Webhook delivery

### 6.1 Delivery flow

```text
Job: webhook_delivery
    │
    ▼
Build webhook payload
    │
    ▼
POST to tenant webhook URL
    │
    ├── 2xx → Record success in webhook_deliveries
    │
    ├── 4xx → Record failure (no retry — client error)
    │
    ├── 5xx → Record failure, schedule retry
    │
    └── Timeout → Record failure, schedule retry
```

### 6.2 Webhook payload

```json
{
  "event_type": "spend_threshold",
  "tenant_id": "uuid",
  "timestamp": "2026-01-15T10:30:00Z",
  "data": {
    "threshold": 0.80,
    "current_spend_cents": 8000,
    "spend_cap_cents": 10000,
    "percentage": 80.0
  }
}
```

### 6.3 Security & SSRF Protection

Dispatching webhooks dynamically opens vectors for Server-Side Request Forgery (SSRF) and DNS rebinding attacks. Validating the URL via standard parsing is insufficient. The webhook dispatcher implements strict IP-level verification:
1. **Asynchronous DNS Resolution**: The hostname is resolved to an IP address before the request is made.
2. **Internal Network Blocking**: The resolved IP is checked against forbidden ranges (`10.0.0.0/8`, `127.0.0.0/8`, `169.254.169.254`, etc.). If it resolves to an internal IP, the webhook is immediately failed.
3. **HTTPS Enforcement**: All webhooks must use `https://`.
4. **TLS Verification**: Strict TLS certificate verification is enforced.

### 6.4 Delivery implementation

```python
async def handle_webhook_delivery(payload: dict) -> None:
    url = payload["url"]
    body = payload["body"]
    tenant_id = payload["tenant_id"]

    delivery = WebhookDelivery(
        tenant_id=tenant_id,
        event_type=payload["event_type"],
        url=url,
        payload=body,
    )

    try:
        response = await http_client.post(
            url,
            json=body,
            timeout=10.0,
            headers={
                "Content-Type": "application/json",
                "X-InfrGate-Event": payload["event_type"],
                "X-InfrGate-Delivery-ID": str(delivery.id),
            },
        )
        delivery.http_status = response.status_code
        delivery.response_body = response.text[:1000]  # Truncate

        if 200 <= response.status_code < 300:
            delivery.status = "success"
        elif 400 <= response.status_code < 500:
            delivery.status = "failed"  # Client error — no retry
        else:
            raise WebhookDeliveryError(f"HTTP {response.status_code}")

    except (httpx.TimeoutException, httpx.ConnectError, WebhookDeliveryError) as e:
        delivery.status = "failed"
        raise  # Will trigger job retry logic
    finally:
        await save_delivery(delivery)
```

### 6.5 Retry policy for webhooks

| Attempt | Delay | Total elapsed |
|---|---|---|
| 1 (initial) | 0s | 0s |
| 2 | 30s | 30s |
| 3 | 60s | 1m 30s |
| 4 | 120s | 3m 30s |
| 5 | 240s | 7m 30s |
| Dead letter | — | — |

---

## 7. Worker lifecycle

### 7.1 Startup

```python
# In Docker Compose, the worker is a separate service:
# command: python -m infrgate.worker

async def startup():
    # Initialize database connection
    # Initialize HTTP client (for webhooks)
    # Set up signal handlers
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
```

### 7.2 Graceful shutdown

```python
shutdown_event = asyncio.Event()

def handle_shutdown(signum, frame):
    logger.info("shutdown_signal_received", signal=signum)
    shutdown_event.set()

# Worker loop checks shutdown_event.is_set() on each iteration
# Current job completes before shutdown
```

**Guarantee:** In-progress jobs always complete before the worker stops. If a job is interrupted (e.g., kill -9), it remains in `running` status and needs a recovery mechanism.

### 7.3 Stuck job recovery

Jobs stuck in `running` status (from crashed workers) are recovered by a periodic cleanup:

```sql
-- Reset jobs that have been running for more than 5 minutes
UPDATE jobs
SET    status = 'pending',
       scheduled_at = now()
WHERE  status = 'running'
  AND  started_at < now() - INTERVAL '5 minutes';
```

This runs as part of the worker's periodic maintenance (every 60 seconds).

---

## 8. Scaling

### 8.1 Concurrency model

- **Multiple workers** can run concurrently (separate processes or containers)
- `FOR UPDATE SKIP LOCKED` ensures no two workers process the same job
- No distributed locks or coordination needed
- Workers are stateless — scale by adding more instances

### 8.2 Docker Compose configuration

```yaml
worker:
  build: .
  command: python -m infrgate.worker
  environment:
    - DATABASE_URL=postgresql+asyncpg://...
    - REDIS_URL=redis://redis:6379/0
    - WORKER_POLL_INTERVAL_S=1
    - WORKER_CONCURRENCY=1
  depends_on:
    - postgres
    - redis
  restart: unless-stopped
```

---

## References

- [03-data-model.md](03-data-model.md) — `jobs` and `webhook_deliveries` table schemas
- [10-usage-accounting.md](10-usage-accounting.md) — Usage aggregation job details
- [04-authentication-tenancy.md](04-authentication-tenancy.md) — Spend cap and billing period reset
