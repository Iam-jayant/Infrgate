/**
 * @infrgate/control-plane v0.2.0
 * 
 * Zero-dependency TypeScript SDK for InfrGate control plane primitives.
 * Provides idempotency, rate limiting, circuit breaking, and intelligent routing
 * for production-grade LLM gateways.
 * 
 * @packageDocumentation
 */

// Idempotency module
export {
  IdempotencyGuard,
  type IdempotencyOptions,
  type CachedResponse,
} from './idempotency.js';

// Rate limiter module
export {
  TokenBucketRateLimiter,
  type RateLimitConfig,
  type RateLimitResult,
} from './rate-limiter.js';

// Circuit breaker module
export {
  ProviderCircuitBreaker,
  CircuitState,
  CircuitOpenError,
  AdapterScorer,
  type CircuitBreakerConfig,
  type CircuitStatus,
  type HealthSignals,
} from './circuit-breaker.js';

// Trace module
export {
  ExecutionTraceBuilder,
  TraceStep,
  type TraceEvent,
  type TraceStepStatus,
  type TraceStepSummary,
} from './trace.js';

// Client module
export {
  InfrGateControlPlane,
  InfrGateClient,
  type ControlPlaneConfig,
  type InfrGateClientConfig,
  type ChatCompletionParams,
  type ChatCompletionResponse,
  type ExecutionResult,
} from './client.js';
