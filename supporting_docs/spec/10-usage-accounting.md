# 10 — Usage Accounting

> Usage ledger design, idempotency, token counting, spend cap enforcement, and billing aggregation for InfrGate.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | Phase 1 (core), Phase 3 (aggregation worker) |
| **Audience** | All contributors |

---

## 1. Overview

Every inference request produces exactly one usage record. The usage ledger is InfrGate's financial system of record — it tracks every token consumed, by which tenant, via which model and provider, at what cost.

### Design principles

1. **Durability:** Usage records are stored in PostgreSQL, never only in Redis
2. **Idempotency:** Composite constraint `UNIQUE(tenant_id, idempotency_key)` ensures at-most-once recording with claim-first pending rows
3. **Completeness:** Every request (success, failure, partial) produces a record
4. **Accuracy:** Token counts come from the provider when available; estimation is the fallback
5. **Tenant isolation:** Usage queries always filter by `tenant_id`

---

## 2. Usage ledger schema

See [03-data-model.md](03-data-model.md#usage_ledger) for the full DDL.

### 2.1 Record fields

| Field | Type | Source | Description |
|---|---|---|---|
| `id` | UUID | Gateway | Primary key |
| `request_id` | UUID | Gateway | Internal request identifier |
| `idempotency_key` | string | Client | Idempotency key (unique per tenant) |
| `tenant_id` | UUID | Auth | Tenant that made the request |
| `model` | string | Request | Model identifier |
| `provider` | string | Routing | Provider that served the request |
| `prompt_tokens` | integer | Provider | Tokens in the prompt |
| `completion_tokens` | integer | Provider | Tokens in the completion |
| `total_tokens` | integer | Provider | Total tokens (prompt + completion) |
| `cost_cents` | bigint | Calculated | Cost in cents (from provider cost config) |
| `status` | string | Gateway | `completed`, `failed`, `partial` |
| `latency_ms` | integer | Gateway | End-to-end latency in milliseconds |
| `metadata` | JSONB | Various | Routing decision, retry count, finish reason |
| `created_at` | timestamp | Gateway | When the record was created |

---

## 3. Recording flow

### 3.1 Non-streaming `[Phase 1]`

```text
Provider response received
    │
    ▼
Extract token counts from provider response
    │
    ▼
Calculate cost (tokens × cost_per_1k_tokens)
    │
    ▼
INSERT INTO usage_ledger ... ON CONFLICT (request_id) DO NOTHING
    │
    ▼
UPDATE tenants SET current_spend_cents = current_spend_cents + cost_cents
```

### 3.2 Streaming `[Phase 3]`

```text
Stream completes (or fails/disconnects)
    │
    ▼
StreamUsageTracker.finalize()
    │
    ▼
Extract/estimate token counts
    │
    ▼
Calculate cost
    │
    ▼
INSERT INTO usage_ledger ... ON CONFLICT (request_id) DO NOTHING
    │
    ▼
UPDATE tenants SET current_spend_cents = current_spend_cents + cost_cents
```

### 3.3 Implementation

```python
async def record_usage(
    db: AsyncSession,
    request_id: str,
    tenant_id: str,
    model: str,
    provider: str,
    response: ProviderResponse | UsageRecord,
    cost_config: dict,
) -> None:
    """
    Record a usage entry in the ledger. Idempotent on request_id.
    Also updates the tenant's current spend.
    """
    # Calculate cost
    model_costs = cost_config.get(model, {"prompt": 0, "completion": 0})
    cost_cents = int(
        (response.prompt_tokens * model_costs["prompt"] / 1000)
        + (response.completion_tokens * model_costs["completion"] / 1000)
    )

    # Insert usage record (idempotent)
    stmt = insert(UsageLedger).values(
        request_id=request_id,
        tenant_id=tenant_id,
        model=model,
        provider=provider,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        total_tokens=response.total_tokens,
        cost_cents=cost_cents,
        status=getattr(response, "status", "completed"),
        latency_ms=response.provider_latency_ms,
        metadata=getattr(response, "metadata", {}),
    ).on_conflict_do_nothing(index_elements=["request_id"])

    result = await db.execute(stmt)

    # Only update spend if the record was actually inserted (not a duplicate)
    if result.rowcount > 0:
        await db.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(
                current_spend_cents=Tenant.current_spend_cents + cost_cents,
                updated_at=func.now(),
            )
        )

    await db.commit()
```

---

## 4. Idempotency

### 4.1 Claim-First Mechanism

InfrGate uses a **claim-first idempotency** model to protect against double-billing during network retries.
The `UNIQUE(tenant_id, idempotency_key)` constraint on `usage_ledger` ensures that each logical request is processed exactly once per tenant.

Before calling an upstream provider, the gateway inserts a `pending` row:
```sql
INSERT INTO usage_ledger (tenant_id, idempotency_key, status, claimed_at, ...)
VALUES (...)
```
If a concurrent request arrives with the same key, it hits the unique constraint. If the existing row is `pending`, it returns `409 Conflict`. If it is `completed` or `failed`, it replays the saved response.

### 4.2 Lease-Expiry and Orphan Recovery (CAS)

If the server crashes mid-flight, a `pending` row could remain stuck indefinitely. InfrGate uses a 90-second lease window. When a duplicate request arrives, if the existing `pending` row is older than 90 seconds, the new request reclaims it using a Compare-and-Swap (CAS) update:
```sql
UPDATE usage_ledger 
SET claimed_at = now() 
WHERE tenant_id = :t AND idempotency_key = :k AND status = 'pending' AND claimed_at = :c
```
This allows the system to self-heal without manual intervention.

### 4.3 Cancellation Handling (asyncio.CancelledError)

During mid-flight execution, client disconnects trigger an `asyncio.CancelledError`. Since Python 3.8, this inherits from `BaseException`, bypassing standard `except Exception` blocks.
To guarantee the usage ledger is always updated out of the `pending` state, we explicitly catch `asyncio.CancelledError` and wrap the database finalization block in an `anyio.CancelScope(shield=True)`.

### 4.4 Edge cases & Limitations

| Scenario | Behavior |
|---|---|
| Normal request | One INSERT (pending), one UPDATE (completed), one spend update |
| Gateway crash mid-flight | Row stays `pending`. Recovers after 90s lease expiry via CAS |
| Duplicate request (concurrent) | `409 Conflict` (mid-flight) |
| Duplicate request (after complete) | Replays original response body and status code |
| Cancellation during `claim_or_replay_request` | Narrow race condition. If cancelled exactly during the initial insert, the shielded block isn't entered yet. Handled safely by the 90s lease expiry. |

---

## 5. Token counting

### 5.1 Source priority

| Priority | Source | When used |
|---|---|---|
| 1 | **Provider response** | Always preferred — most accurate |
| 2 | **Estimation** | Fallback when provider doesn't report tokens |

### 5.2 Token estimation (fallback)

When the provider doesn't include token counts (e.g., streaming disconnect before final chunk):

```python
def _estimate_tokens(text: str) -> int:
    """
    Rough token estimation using character count.
    Average English: ~4 characters per token.
    This is a fallback — provider-reported counts are always preferred.
    """
    return max(1, len(text) // 4)
```

### 5.3 Prompt token estimation (for TPM rate limiting)

Before the provider call, the gateway estimates prompt tokens for TPM rate limiting:

```python
def estimate_prompt_tokens(messages: list[dict]) -> int:
    """Estimate prompt tokens from message content."""
    total_chars = sum(len(m.get("content", "")) for m in messages)
    overhead = len(messages) * 4  # Per-message overhead
    return max(1, (total_chars + overhead) // 4)
```

---

## 6. Cost calculation

### 6.1 Cost formula

```
cost_cents = (prompt_tokens × prompt_rate / 1000) + (completion_tokens × completion_rate / 1000)
```

Rates are configured per model in `provider_configs.cost_per_1k_tokens`.

### 6.2 Cost configuration

```json
{
  "gemini-2.0-flash": { "prompt": 0.10, "completion": 0.40 },
  "gemini-2.5-pro":   { "prompt": 1.25, "completion": 10.0 },
  "gpt-4o":           { "prompt": 2.50, "completion": 10.0 },
  "gpt-4o-mini":      { "prompt": 0.15, "completion": 0.60 }
}
```

### 6.3 Free-tier models

Gemini models via Google AI Studio are free. Their costs are configured as `0.0` but usage records are still created for metering purposes.

---

## 7. Spend cap enforcement

### 7.1 Pre-flight check (before provider call)

```python
if tenant.spend_cap_cents is not None:
    if tenant.current_spend_cents >= tenant.spend_cap_cents:
        raise SpendCapExceeded(tenant)
```

### 7.2 Post-inference update

```sql
UPDATE tenants
SET current_spend_cents = current_spend_cents + :cost_cents,
    updated_at = now()
WHERE id = :tenant_id;
```

### 7.3 Spend alerts `[Phase 3]`

When `current_spend_cents` crosses a threshold (e.g., 80% of `spend_cap_cents`), the worker enqueues a webhook:

```python
thresholds = [0.50, 0.80, 0.90, 1.00]  # 50%, 80%, 90%, 100%

for threshold in thresholds:
    threshold_cents = int(tenant.spend_cap_cents * threshold)
    if previous_spend < threshold_cents <= new_spend:
        await enqueue_job("spend_alert", {
            "tenant_id": str(tenant.id),
            "threshold": threshold,
            "current_spend_cents": new_spend,
            "spend_cap_cents": tenant.spend_cap_cents,
        })
```

---

## 8. Usage aggregation `[Phase 3]`

### 8.1 Aggregation job

The background worker periodically aggregates raw usage records into summary statistics:

```python
async def aggregate_usage(tenant_id: str, period: str):
    """
    Aggregate usage records for a tenant and period.
    Creates/updates summary statistics for billing and reporting.
    """
    # Query raw usage records for the period
    records = await db.execute(
        select(UsageLedger)
        .where(
            UsageLedger.tenant_id == tenant_id,
            UsageLedger.created_at >= period_start,
            UsageLedger.created_at < period_end,
        )
    )

    # Aggregate by model
    summary = {}
    for record in records:
        model = record.model
        if model not in summary:
            summary[model] = {"requests": 0, "total_tokens": 0, "cost_cents": 0}
        summary[model]["requests"] += 1
        summary[model]["total_tokens"] += record.total_tokens
        summary[model]["cost_cents"] += record.cost_cents

    return summary
```

### 8.2 Billing period reset

At the start of each billing period, the tenant's `current_spend_cents` is reset:

```sql
UPDATE tenants
SET current_spend_cents = 0,
    updated_at = now()
WHERE status = 'active';
```

This is triggered by a scheduled job in the background worker `[Phase 3]`.

---

## 9. Usage status values

| Status | Meaning | Phase |
|---|---|---|
| `completed` | Inference succeeded; full token counts available | 1 |
| `failed` | Provider error; best-known tokens recorded | 1 |
| `partial` | Client disconnected during stream; accumulated tokens recorded | 3 |

---

## References

- [03-data-model.md](03-data-model.md) — Usage ledger table schema
- [04-authentication-tenancy.md](04-authentication-tenancy.md) — Spend cap enforcement
- [09-streaming.md](09-streaming.md) — Stream usage tracking
- [11-background-worker.md](11-background-worker.md) — Aggregation and spend alert jobs
- [02-api-design.md](02-api-design.md) — Usage query endpoints
