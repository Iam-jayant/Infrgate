# 08 — Reliability

> Timeout, retry, circuit breaker, and failover design for InfrGate.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | Phase 2 |
| **Audience** | All contributors |

---

## 1. Overview

The reliability layer wraps every upstream provider call with timeout enforcement, retry logic, circuit breaker protection, and failover orchestration. Its goal is to **contain provider failures** so that a single unhealthy provider does not degrade the entire system.

### Reliability stack (applied in order)

```text
Request
  │
  ▼
┌──────────────┐
│   Failover   │   ← Orchestrates retries across providers
│              │
│ ┌──────────┐ │
│ │ Circuit  │ │   ← Checks/updates circuit breaker state
│ │ Breaker  │ │
│ │          │ │
│ │┌────────┐│ │
│ ││ Retry  ││ │   ← Retries with backoff on retryable errors
│ ││        ││ │
│ ││┌──────┐││ │
│ │││Timeout│││ │   ← Enforces per-request timeout
│ ││└──────┘││ │
│ │└────────┘│ │
│ └──────────┘ │
└──────────────┘
  │
  ▼
Provider response / error
```

---

## 2. Timeout policy

### 2.1 Timeout configuration

```python
@dataclass
class TimeoutConfig:
    connect_timeout_s: float = 5.0     # TCP connection establishment
    read_timeout_s: float = 30.0       # Waiting for first byte of response
    total_timeout_s: float = 60.0      # Total wall-clock time for the request

    # Streaming-specific [Phase 3]
    stream_read_timeout_s: float = 10.0  # Between SSE chunks
```

Timeouts are configured per-provider in `provider_configs.timeout_config`.

### 2.2 Timeout enforcement

```python
async def execute_with_timeout(
    adapter: ProviderAdapter,
    request: ProviderRequest,
    timeout: TimeoutConfig,
) -> ProviderResponse:
    try:
        return await asyncio.wait_for(
            adapter.complete(request),
            timeout=timeout.total_timeout_s,
        )
    except asyncio.TimeoutError:
        raise ProviderTimeoutError(adapter.provider_name, timeout.total_timeout_s)
```

### 2.3 Timeout values by provider

| Provider | Connect | Read | Total |
|---|---|---|---|
| Gemini | 5s | 30s | 60s |
| OpenAI | 5s | 30s | 60s |
| Fake (testing) | 1s | 5s | 10s |

---

## 3. Retry strategy

### 3.1 Retry policy

```python
@dataclass
class RetryPolicy:
    max_attempts: int = 3              # Total attempts (1 initial + 2 retries)
    base_delay_s: float = 0.5          # Base delay for exponential backoff
    max_delay_s: float = 8.0           # Maximum delay cap
    jitter: bool = True                # Add random jitter to prevent thundering herd
    retryable_errors: set = field(default_factory=lambda: {
        ProviderTimeoutError,
        ProviderRateLimitError,
    })
```

### 3.2 Exponential backoff with jitter

```python
def calculate_delay(attempt: int, policy: RetryPolicy) -> float:
    """
    Calculate delay before the next retry.
    Uses exponential backoff with optional full jitter.

    attempt: 0-indexed retry number (0 = first retry)
    """
    delay = min(
        policy.base_delay_s * (2 ** attempt),
        policy.max_delay_s,
    )
    if policy.jitter:
        delay = random.uniform(0, delay)  # Full jitter
    return delay
```

**Delay progression (without jitter):**

| Attempt | Delay |
|---|---|
| Initial | 0 (immediate) |
| Retry 1 | 0.5s |
| Retry 2 | 1.0s |
| Retry 3 (if configured) | 2.0s |

### 3.3 Error classification

| Error | Retryable | Rationale |
|---|---|---|
| `ProviderTimeoutError` | ✅ | Transient — provider may recover |
| `ProviderRateLimitError` | ✅ | Transient — rate limit will reset |
| `ProviderError(status=500)` | ✅ | Server-side transient error |
| `ProviderError(status=503)` | ✅ | Service temporarily unavailable |
| `ProviderAuthError` | ❌ | Credentials are wrong — retrying won't help |
| `ProviderError(status=400)` | ❌ | Bad request — client error |
| `ProviderError(status=404)` | ❌ | Model not found |

### 3.4 Retry execution

```python
async def execute_with_retry(
    adapter: ProviderAdapter,
    request: ProviderRequest,
    timeout: TimeoutConfig,
    retry_policy: RetryPolicy,
) -> ProviderResponse:
    """Execute a provider call with retry logic."""
    last_error = None

    for attempt in range(retry_policy.max_attempts):
        try:
            return await execute_with_timeout(adapter, request, timeout)
        except ProviderError as e:
            last_error = e
            if not e.retryable or attempt == retry_policy.max_attempts - 1:
                raise

            delay = calculate_delay(attempt, retry_policy)
            logger.warning(
                "provider_retry",
                provider=adapter.provider_name,
                attempt=attempt + 1,
                max_attempts=retry_policy.max_attempts,
                delay_s=delay,
                error=str(e),
                request_id=request.request_id,
            )
            await asyncio.sleep(delay)

    raise last_error  # Should not reach here
```

---

## 4. Circuit breaker

### 4.1 State machine

```text
                success
           ┌──────────────┐
           │              │
           ▼              │
    ┌──────────┐    ┌─────┴──────┐    ┌──────────────┐
    │          │    │            │    │              │
    │  CLOSED  │───▶│   OPEN    │───▶│  HALF_OPEN   │
    │          │    │            │    │              │
    └──────────┘    └────────────┘    └──────┬───────┘
         ▲               ▲                  │
         │               │                  │
         │          failure threshold       │
         │               met               │
         │                                  │
         └──────────────────────────────────┘
              success_threshold met     failure
              (probe succeeded)     (probe failed → reopen)
```

### 4.2 States

| State | Behavior | Transition trigger |
|---|---|---|
| **CLOSED** | Normal operation. All requests pass through. Failures are counted. | → OPEN when `failure_count >= failure_threshold` within the window |
| **OPEN** | All requests are **immediately rejected** without calling the provider. | → HALF_OPEN after `recovery_timeout` elapses |
| **HALF_OPEN** | A limited number of **probe requests** are allowed through. | → CLOSED if `success_threshold` probes succeed. → OPEN if any probe fails. |

### 4.3 Configuration

```python
@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5          # Failures to trip the circuit
    failure_window_s: int = 60          # Window in which failures are counted
    recovery_timeout_s: int = 30        # Time in OPEN before transitioning to HALF_OPEN
    success_threshold: int = 2          # Successful probes needed to close
    half_open_max_requests: int = 3     # Max concurrent requests in HALF_OPEN
```

### 4.4 Redis state management

Circuit breaker state is stored in Redis for cross-instance consistency:

```python
async def get_circuit_state(redis: Redis, provider: str) -> CircuitState:
    key = f"infrgate:circuit:{provider}"
    data = await redis.hgetall(key)
    if not data:
        return CircuitState.CLOSED

    state = data.get("state", "closed")

    if state == "open":
        opened_at = float(data.get("opened_at", 0))
        if time.time() - opened_at >= CIRCUIT_CONFIG.recovery_timeout_s:
            # Transition to half_open
            await redis.hset(key, mapping={
                "state": "half_open",
                "half_open_at": str(time.time()),
                "success_count": "0",
            })
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    return CircuitState(state)

async def record_circuit_result(
    redis: Redis,
    provider: str,
    success: bool,
    config: CircuitBreakerConfig,
):
    key = f"infrgate:circuit:{provider}"
    state = await get_circuit_state(redis, provider)

    if state == CircuitState.CLOSED:
        if not success:
            failure_count = await redis.hincrby(key, "failure_count", 1)
            await redis.hset(key, "last_failure", str(time.time()))
            if failure_count >= config.failure_threshold:
                # Trip the circuit
                await redis.hset(key, mapping={
                    "state": "open",
                    "opened_at": str(time.time()),
                    "failure_count": "0",
                })
                logger.error("circuit_opened", provider=provider)
        else:
            # Reset failure count on success
            await redis.hset(key, "failure_count", "0")

    elif state == CircuitState.HALF_OPEN:
        if success:
            success_count = await redis.hincrby(key, "success_count", 1)
            if success_count >= config.success_threshold:
                # Close the circuit
                await redis.hset(key, mapping={
                    "state": "closed",
                    "failure_count": "0",
                    "success_count": "0",
                })
                logger.info("circuit_closed", provider=provider)
        else:
            # Re-open the circuit
            await redis.hset(key, mapping={
                "state": "open",
                "opened_at": str(time.time()),
                "success_count": "0",
            })
            logger.warning("circuit_reopened", provider=provider)
```

---

## 5. Failover

### 5.1 Failover flow

When the primary provider fails after exhausting retries (or its circuit is open), the reliability layer attempts the next eligible provider:

```text
Request for model "gpt-4o"
  │
  ▼
Provider 1: openai (priority 1)
  ├── Circuit: CLOSED → attempt
  ├── Attempt 1: timeout → retry
  ├── Attempt 2: timeout → retry
  └── Attempt 3: timeout → circuit trip → FAILOVER
  │
  ▼
Provider 2: gemini (priority 2, if model supported)
  ├── Circuit: CLOSED → attempt
  └── Attempt 1: success → return response
```

### 5.2 Failover implementation

```python
async def execute_with_failover(
    providers: list[EligibleProvider],
    request: ProviderRequest,
    redis: Redis,
) -> tuple[ProviderResponse, RoutingDecision]:
    """
    Execute request with failover across providers.
    Tries providers in order until one succeeds or all fail.
    """
    errors = []
    decision = RoutingDecision(
        request_id=request.request_id,
        requested_model=request.model,
        eligible_providers=[p.adapter.provider_name for p in providers],
    )

    for i, provider in enumerate(providers):
        # Check circuit breaker
        circuit_state = await get_circuit_state(redis, provider.adapter.provider_name)
        if circuit_state == CircuitState.OPEN:
            errors.append(f"{provider.adapter.provider_name}: circuit open")
            continue

        try:
            response = await execute_with_retry(
                provider.adapter,
                request,
                provider.config.timeout_config,
                provider.config.retry_policy,
            )
            # Record success for circuit breaker
            await record_circuit_result(redis, provider.adapter.provider_name, True, CB_CONFIG)

            decision.selected_provider = provider.adapter.provider_name
            decision.fallback_used = i > 0
            decision.reason = "fallback" if i > 0 else "primary"

            return response, decision

        except ProviderError as e:
            # Record failure for circuit breaker
            await record_circuit_result(redis, provider.adapter.provider_name, False, CB_CONFIG)
            errors.append(f"{provider.adapter.provider_name}: {e.message}")

            if not e.retryable:
                raise  # Non-retryable errors abort immediately

    # All providers failed
    raise HTTPException(503, detail={
        "error": {
            "type": "provider_unavailable",
            "message": "All providers failed.",
            "details": errors,
        }
    })
```

---

## 6. Reliability metrics

| Metric | Type | Description |
|---|---|---|
| `infrgate_provider_requests_total` | Counter | Provider calls by provider, status |
| `infrgate_provider_retries_total` | Counter | Retry attempts by provider |
| `infrgate_provider_timeouts_total` | Counter | Timeout events by provider |
| `infrgate_circuit_state_changes_total` | Counter | Circuit state transitions |
| `infrgate_failover_total` | Counter | Failover events |
| `infrgate_provider_latency_seconds` | Histogram | Provider response latency |

---

## References

- [06-provider-adapters.md](06-provider-adapters.md) — Provider error hierarchy
- [07-routing-engine.md](07-routing-engine.md) — Provider selection and health filtering
- [03-data-model.md](03-data-model.md) — Redis circuit breaker key design
- [12-observability.md](12-observability.md) — Full metrics catalog
