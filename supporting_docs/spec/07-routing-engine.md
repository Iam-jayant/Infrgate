# 07 — Routing Engine

> Model-to-provider resolution, priority-based routing, and health-aware intelligent routing for InfrGate.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | Phase 2 (priority routing), Phase 4 (EWMA health scoring, cost-aware routing) |
| **Audience** | All contributors |

---

## 1. Overview

The routing engine decides **which provider** handles a given inference request. It evolves across phases:

| Phase | Routing behavior |
|---|---|
| **Phase 1** | No routing — Gemini is the only provider; direct adapter call |
| **Phase 2** | Priority-based routing with health filtering (circuit breaker state) |
| **Phase 4** | EWMA-scored health-aware routing with cost and capability constraints |

---

## 2. Routing flow

```text
┌─────────────────────────────────┐
│ Incoming request                │
│ model = "gpt-4o"                │
└──────────────┬──────────────────┘
               │
      ┌────────▼────────┐
      │ Model resolution│
      │ "gpt-4o" →      │
      │ [openai, gemini] │    ← providers that support this model
      └────────┬────────┘
               │
      ┌────────▼──────────┐
      │ Health filtering  │   [Phase 2]
      │ Remove providers  │
      │ with circuit OPEN │
      └────────┬──────────┘
               │
      ┌────────▼──────────┐
      │ Scoring           │   [Phase 4]
      │ EWMA health +     │
      │ cost + capability  │
      └────────┬──────────┘
               │
      ┌────────▼──────────┐
      │ Selection         │
      │ Highest scoring   │
      │ provider          │
      └────────┬──────────┘
               │
      ┌────────▼──────────┐
      │ Decision log      │
      │ Record inputs,    │
      │ scores, choice    │
      └──────────────────┘
```

---

## 3. Model resolution `[Phase 2]`

### 3.1 Resolution logic

```python
def resolve_providers(
    model: str,
    registry: ProviderRegistry,
    provider_configs: list[ProviderConfig],
) -> list[EligibleProvider]:
    """
    Resolve a model name to an ordered list of eligible providers.
    Returns providers sorted by priority (lower number = higher priority).
    """
    eligible = []
    for config in provider_configs:
        if not config.enabled:
            continue
        adapter = registry.get_by_name(config.provider_name)
        if model in adapter.supported_models or model in _get_aliases(config, model):
            eligible.append(EligibleProvider(
                adapter=adapter,
                config=config,
                priority=config.priority,
            ))

    if not eligible:
        raise HTTPException(400, detail={
            "error": {
                "type": "invalid_request",
                "message": f"No provider available for model '{model}'."
            }
        })

    return sorted(eligible, key=lambda p: p.priority)
```

### 3.2 Model aliases

Providers can define aliases for models in their config:

```json
{
  "models": [
    {
      "model_id": "gemini-2.0-flash",
      "aliases": ["gemini-flash", "flash"]
    }
  ]
}
```

If a request uses an alias, the routing engine resolves it to the canonical model ID before passing to the adapter.

---

## 4. Health filtering `[Phase 2]`

After model resolution, providers with an **open circuit breaker** are filtered out:

```python
async def filter_healthy(
    providers: list[EligibleProvider],
    redis: Redis,
) -> list[EligibleProvider]:
    """Remove providers with open circuit breakers."""
    healthy = []
    for provider in providers:
        state = await get_circuit_state(redis, provider.adapter.provider_name)
        if state != CircuitState.OPEN:
            healthy.append(provider)

    if not healthy:
        # All providers are down — raise 503
        raise HTTPException(503, detail={
            "error": {
                "type": "provider_unavailable",
                "message": "All providers for this model are currently unavailable."
            }
        })

    return healthy
```

See [08-reliability.md](08-reliability.md) for the circuit breaker FSM.

---

## 5. EWMA health scoring `[Phase 4]`

### 5.1 Health signals

Each provider continuously accumulates health signals from completed requests:

| Signal | Source | Formula |
|---|---|---|
| **Error rate** | Provider responses | EWMA of `1` (error) or `0` (success) |
| **Latency** | Provider responses | EWMA of response latency in ms |
| **Availability** | Circuit breaker | `1.0` if CLOSED, `0.5` if HALF_OPEN, `0.0` if OPEN |

### 5.2 EWMA calculation

Exponentially Weighted Moving Average with configurable decay factor:

```python
def ewma_update(current: float, new_sample: float, alpha: float = 0.3) -> float:
    """
    Update EWMA with a new sample.
    alpha: smoothing factor (0 < alpha <= 1)
           Higher alpha = more weight on recent samples
           Lower alpha = smoother, slower to react
    """
    return alpha * new_sample + (1 - alpha) * current
```

**After each provider call:**

```python
async def record_health_signal(
    redis: Redis,
    provider_name: str,
    latency_ms: int,
    is_error: bool,
    alpha: float = 0.3,
):
    key = f"infrgate:health:{provider_name}"

    current = await redis.hgetall(key)
    ewma_latency = ewma_update(
        float(current.get("ewma_latency_ms", latency_ms)),
        latency_ms,
        alpha,
    )
    ewma_error = ewma_update(
        float(current.get("ewma_error_rate", 0.0)),
        1.0 if is_error else 0.0,
        alpha,
    )

    await redis.hset(key, mapping={
        "ewma_latency_ms": str(ewma_latency),
        "ewma_error_rate": str(ewma_error),
        "last_updated": str(time.time()),
        "sample_count": str(int(current.get("sample_count", 0)) + 1),
    })
    await redis.expire(key, 300)  # 5 min TTL
```

### 5.3 Composite scoring

Each eligible provider receives a composite score based on configurable weights:

```python
@dataclass
class RoutingWeights:
    """Configurable weights for routing score calculation."""
    availability: float = 0.35    # Circuit breaker state
    error_rate: float = 0.30      # EWMA error rate (inverted — lower is better)
    latency: float = 0.20         # EWMA latency (inverted — lower is better)
    cost: float = 0.15            # Cost per 1K tokens (inverted)

def calculate_score(
    provider: EligibleProvider,
    health: HealthSignals,
    weights: RoutingWeights,
) -> float:
    """
    Calculate composite routing score for a provider.
    Higher score = more preferred.
    All components are normalized to [0.0, 1.0].
    """
    # Availability: 1.0 (closed), 0.5 (half_open), 0.0 (open)
    availability_score = health.availability

    # Error rate: invert so lower error rate = higher score
    error_score = 1.0 - health.ewma_error_rate

    # Latency: normalize and invert (lower latency = higher score)
    # Using 5000ms as the reference maximum
    latency_score = max(0.0, 1.0 - (health.ewma_latency_ms / 5000.0))

    # Cost: normalize and invert (lower cost = higher score)
    # Using 10 cents/1K as the reference maximum
    cost_score = max(0.0, 1.0 - (provider.cost_per_1k / 10.0))

    return (
        weights.availability * availability_score
        + weights.error_rate * error_score
        + weights.latency * latency_score
        + weights.cost * cost_score
    )
```

### 5.4 Provider selection

```python
async def select_provider(
    eligible: list[EligibleProvider],
    redis: Redis,
    weights: RoutingWeights,
) -> tuple[EligibleProvider, RoutingDecision]:
    """Select the best provider based on composite scores."""
    scores = {}
    for provider in eligible:
        health = await get_health_signals(redis, provider.adapter.provider_name)
        score = calculate_score(provider, health, weights)
        scores[provider.adapter.provider_name] = {
            "health": health.availability,
            "error": 1.0 - health.ewma_error_rate,
            "latency": max(0.0, 1.0 - health.ewma_latency_ms / 5000.0),
            "cost": max(0.0, 1.0 - provider.cost_per_1k / 10.0),
            "total": score,
        }

    best = max(eligible, key=lambda p: scores[p.adapter.provider_name]["total"])

    decision = RoutingDecision(
        eligible_providers=[p.adapter.provider_name for p in eligible],
        selected_provider=best.adapter.provider_name,
        reason="highest_score",
        scores=scores,
    )

    return best, decision
```

---

## 6. Routing decision log `[Phase 4]`

Every routing decision is logged for observability and debugging:

```python
@dataclass
class RoutingDecision:
    request_id: str
    requested_model: str
    eligible_providers: list[str]
    selected_provider: str
    reason: str                       # "highest_priority", "highest_score", "only_available"
    scores: dict[str, dict] | None    # Composite scores (Phase 4)
    fallback_used: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)
```

Decision logs are:
- Written to the structured log (always)
- Stored in memory (ring buffer, last 1000 decisions) for the admin API `[Phase 4]`
- Queryable via `GET /admin/routing/decisions` `[Phase 4]`

---

## 7. Cost model `[Phase 4]`

### 7.1 Cost per 1K tokens

Costs are configured per model in `provider_configs`:

```json
{
  "cost_per_1k_tokens": {
    "gemini-2.0-flash": { "prompt": 0.10, "completion": 0.40 },
    "gpt-4o": { "prompt": 2.50, "completion": 10.0 }
  }
}
```

### 7.2 Cost estimation

Before routing, the engine estimates the cost of the request for each provider:

```python
def estimate_cost(provider: EligibleProvider, estimated_tokens: int) -> float:
    """Estimate cost in cents for the given token count."""
    cost_config = provider.config.cost_per_1k_tokens.get(provider.model, {})
    prompt_cost = cost_config.get("prompt", 0) * estimated_tokens / 1000
    return prompt_cost  # Completion cost unknown at routing time
```

---

## 8. Phase 2 routing (simplified)

In Phase 2, routing is **priority-based without scoring**:

```python
async def route_request_phase2(
    model: str,
    registry: ProviderRegistry,
    configs: list[ProviderConfig],
    redis: Redis,
) -> EligibleProvider:
    """Phase 2: Priority-based routing with health filtering."""
    # 1. Resolve model to eligible providers
    eligible = resolve_providers(model, registry, configs)

    # 2. Filter out unhealthy providers (circuit open)
    healthy = await filter_healthy(eligible, redis)

    # 3. Return highest priority (lowest number)
    return healthy[0]
```

---

## References

- [06-provider-adapters.md](06-provider-adapters.md) — Provider interface and registry
- [08-reliability.md](08-reliability.md) — Circuit breaker state for health filtering
- [03-data-model.md](03-data-model.md) — `provider_configs` table and Redis health keys
- [02-api-design.md](02-api-design.md) — Routing decisions admin endpoint
