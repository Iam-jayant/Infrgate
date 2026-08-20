# 05 — Rate Limiting

> Redis-backed sliding window rate limiting design for InfrGate.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | Phase 1 (core) |
| **Audience** | All contributors |

---

## 1. Overview

InfrGate enforces per-tenant rate limits to protect upstream providers and ensure fair resource allocation. Rate limiting runs **after authentication** and **before provider calls**, so rejected requests never consume provider quota.

### Rate limit dimensions

| Dimension | Unit | Key | Phase |
|---|---|---|---|
| **Requests per minute (RPM)** | Requests | `infrgate:ratelimit:{tenant_id}:rpm:{window}` | 1 |
| **Tokens per minute (TPM)** | Tokens (estimated) | `infrgate:ratelimit:{tenant_id}:tpm:{window}` | 1 |

---

## 2. Algorithm: Sliding window log

InfrGate uses a **sliding window log** algorithm backed by Redis sorted sets. This provides precise rate limiting without the burstiness of fixed windows.

### 2.1 How it works

```text
Window: 60 seconds (for RPM)
Current time: T

1. Remove all entries older than T - 60s from the sorted set
2. Count remaining entries
3. If count >= limit → REJECT (429)
4. If count < limit → ADD current request, ALLOW
```

### 2.2 Redis operations (atomic)

All operations execute in a single Redis pipeline for atomicity:

```python
async def check_rate_limit(
    redis: Redis,
    tenant_id: str,
    limit: int,
    window_seconds: int = 60,
) -> RateLimitResult:
    """
    Sliding window rate limit check using Redis sorted sets.
    Returns RateLimitResult with allowed/denied status and metadata.
    """
    key = f"infrgate:ratelimit:{tenant_id}:rpm:{window_seconds}"
    now = time.time()
    window_start = now - window_seconds

    pipe = redis.pipeline(transaction=True)

    # 1. Remove expired entries
    pipe.zremrangebyscore(key, 0, window_start)

    # 2. Count current entries
    pipe.zcard(key)

    # 3. Add this request (optimistic — removed if denied)
    request_id = str(uuid.uuid4())
    pipe.zadd(key, {request_id: now})

    # 4. Set TTL (window + buffer)
    pipe.expire(key, window_seconds + 10)

    results = await pipe.execute()
    current_count = results[1]  # zcard result

    if current_count >= limit:
        # Over limit — remove the optimistically added entry
        await redis.zrem(key, request_id)
        return RateLimitResult(
            allowed=False,
            limit=limit,
            remaining=0,
            reset_at=int(now + window_seconds),
            retry_after=_calc_retry_after(redis, key, window_start, window_seconds),
        )

    return RateLimitResult(
        allowed=True,
        limit=limit,
        remaining=limit - current_count - 1,
        reset_at=int(now + window_seconds),
    )
```

### 2.3 TPM rate limiting

Token-per-minute limiting is similar but uses **estimated prompt tokens** (counted before the provider call) as the score weight:

```python
async def check_tpm_limit(
    redis: Redis,
    tenant_id: str,
    estimated_tokens: int,
    limit: int,
    window_seconds: int = 60,
) -> RateLimitResult:
    key = f"infrgate:ratelimit:{tenant_id}:tpm:{window_seconds}"
    now = time.time()
    window_start = now - window_seconds

    pipe = redis.pipeline(transaction=True)
    pipe.zremrangebyscore(key, 0, window_start)

    # Sum token weights of all entries in window
    pipe.zrangebyscore(key, window_start, now, withscores=True)

    results = await pipe.execute()
    entries = results[1]
    current_tokens = sum(score for _, score in entries)

    if current_tokens + estimated_tokens > limit:
        return RateLimitResult(allowed=False, limit=limit, remaining=0, ...)

    # Add with token count as score
    await redis.zadd(key, {f"{uuid.uuid4()}:{estimated_tokens}": now})
    await redis.expire(key, window_seconds + 10)

    return RateLimitResult(
        allowed=True,
        limit=limit,
        remaining=max(0, limit - current_tokens - estimated_tokens),
        ...
    )
```

---

## 3. Rate limit resolution

Rate limits are resolved per-tenant using the plan model:

```text
1. Tenant config override (config.rpm_limit, config.tpm_limit)
2. Plan defaults (PLAN_DEFAULTS[tenant.plan])
3. System defaults (DEFAULT_RPM, DEFAULT_TPM from env)
```

See [04-authentication-tenancy.md](04-authentication-tenancy.md#plan-resolution) for the full resolution logic.

---

## 4. Response headers

Every successful response includes rate limit headers:

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 1700000060
```

| Header | Description |
|---|---|
| `X-RateLimit-Limit` | Maximum requests allowed in the current window |
| `X-RateLimit-Remaining` | Requests remaining in the current window |
| `X-RateLimit-Reset` | Unix timestamp when the current window resets |

On rate limit exceeded (429):

```
HTTP/1.1 429 Too Many Requests
Retry-After: 3
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1700000060
Content-Type: application/json

{
  "error": {
    "type": "rate_limit_exceeded",
    "message": "Rate limit exceeded. Retry after 3 seconds.",
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "code": 429
  }
}
```

---

## 5. Retry-After calculation

The `Retry-After` value tells the client how long to wait before their next request will be accepted:

```python
async def _calc_retry_after(
    redis: Redis,
    key: str,
    window_start: float,
    window_seconds: int,
) -> int:
    """
    Calculate seconds until the oldest entry in the window expires,
    freeing a slot for the next request.
    """
    oldest = await redis.zrangebyscore(key, window_start, "+inf", start=0, num=1, withscores=True)
    if oldest:
        _, oldest_score = oldest[0]
        seconds_until_free = int(oldest_score + window_seconds - time.time()) + 1
        return max(1, seconds_until_free)
    return 1
```

---

## 6. Redis failure handling

Rate limiting must **not block requests** if Redis is temporarily unavailable.

### 6.1 Strategy: fail-open with logging

```python
async def rate_limit_middleware(request: Request, tenant: Tenant):
    try:
        result = await check_rate_limit(redis, tenant.id, resolve_rpm_limit(tenant))
        if not result.allowed:
            raise HTTPException(429, ...)
        # Attach headers for response
        request.state.rate_limit = result
    except RedisError as e:
        # Log warning — rate limiting degraded
        logger.warning(
            "rate_limit_degraded",
            tenant_id=str(tenant.id),
            error=str(e),
        )
        # Allow request through (fail-open)
        request.state.rate_limit = RateLimitResult(
            allowed=True, limit=0, remaining=0, reset_at=0, degraded=True
        )
```

### 6.2 Design rationale

| Alternative | Rejected because |
|---|---|
| **Fail-closed** (deny all) | A Redis blip would block all tenants — availability priority over strictness |
| **In-memory fallback** | Inaccurate in multi-instance deployments; complex state sync |
| **No action** | Silent failure; operators wouldn't know rate limiting is degraded |

**Choice:** Fail-open with structured warning log. Operators set alerts on `rate_limit_degraded` log events.

---

## 7. Data model

Rate limit state lives entirely in Redis. No PostgreSQL tables are involved.

| Key | Type | TTL | Contents |
|---|---|---|---|
| `infrgate:ratelimit:{tenant_id}:rpm:60` | ZSET | 70s | Members: request IDs, Scores: timestamps |
| `infrgate:ratelimit:{tenant_id}:tpm:60` | ZSET | 70s | Members: request IDs with token count, Scores: timestamps |

See [03-data-model.md](03-data-model.md#redis-key-design) for the full Redis key namespace.

---

## 8. Testing strategy

| Test | Type | Description |
|---|---|---|
| Allow under limit | Unit | Send N < limit requests → all pass |
| Reject at limit | Unit | Send N = limit requests → Nth+1 is rejected with 429 |
| Window slide | Unit | Send limit requests, wait window, send again → pass |
| Retry-After accuracy | Unit | After rejection, verify Retry-After header value |
| Redis down (fail-open) | Integration | Kill Redis mid-test → requests still pass, warning logged |
| Multi-tenant isolation | Integration | Tenant A at limit → Tenant B unaffected |
| TPM limit | Unit | Send request with estimated tokens exceeding TPM → 429 |

---

## References

- [03-data-model.md](03-data-model.md) — Redis key namespace
- [04-authentication-tenancy.md](04-authentication-tenancy.md) — Plan model and limit resolution
- [02-api-design.md](02-api-design.md) — Rate limit headers and error codes
