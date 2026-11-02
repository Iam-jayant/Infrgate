# Requirements Document

**Feature Name:** `typescript-control-plane-sdk`  
**Status:** Draft  
**Created:** 2024-01-XX  
**Last Updated:** 2024-01-XX

---

## Introduction

### Purpose

Package InfrGate's core Intelligent Inference Control Plane primitives into a standalone, zero-dependency TypeScript NPM package that can be published to npm and installed directly in Next.js/Node.js AI gateways, enabling developers to build production-grade LLM infrastructure with idempotency, rate limiting, circuit breaking, and intelligent routing without requiring the full Python backend.

### Background

InfrGate's Python-based gateway (`src/infrgate/services/`) implements battle-tested control plane primitives including:

- **Idempotency Guard**: SHA-256 fingerprinting with TTL replay cache for exactly-once execution semantics
- **Token Bucket Rate Limiter**: Per-key token bucket algorithm for RPM/TPM limits with sliding windows
- **Circuit Breaker**: State machine (CLOSED → OPEN → HALF_OPEN) with EWMA health scoring
- **Execution Trace Builder**: 6-step gateway execution trace for observability
- **OpenAI-Compatible Client**: Drop-in replacement client wrapper

JavaScript/TypeScript developers building Next.js AI applications need these same reliability primitives but cannot easily consume Python packages. This SDK ports the core algorithms to TypeScript, enabling:

1. **Embedded Control Planes**: Next.js API routes can use `IdempotencyGuard` directly without Redis
2. **Edge Deployment**: Cloudflare Workers/Vercel Edge can use zero-dependency modules
3. **Framework Agnostic**: Works in Node.js, Deno, Bun, browser (where applicable)

### Success Criteria

- **Zero External Runtime Dependencies**: No npm packages required at runtime (dev deps allowed)
- **Tree-Shakeable**: ESM exports allow bundlers to eliminate unused code
- **Type-Safe**: Full TypeScript declarations with strict mode enabled
- **Portable Crypto**: Uses Web Crypto API (Node 16+/browser) with Node.js `crypto` fallback
- **Build Output**: CommonJS (.js), ESM (.mjs), and TypeScript (.d.ts) in `dist/`
- **Documentation**: Professional README with architecture diagrams, installation, examples
- **Test Coverage**: ≥80% coverage with Vitest, including edge cases

---

## Requirements

### Functional Requirements

#### Package Configuration & Build System

**FR-1.1**: The SDK MUST be published as `@infrgate/control-plane` version `0.2.0`.

**FR-1.2**: The `package.json` MUST declare:
- `"type": "module"` (ESM-first)
- `"main"`: CommonJS entry point (`dist/index.js`)
- `"module"`: ESM entry point (`dist/index.mjs`)
- `"types"`: TypeScript declarations (`dist/index.d.ts`)
- `"exports"` map with conditional exports for Node.js/default/types

**FR-1.3**: The build system MUST use `tsup` configured to:
- Emit dual formats: CommonJS (`cjs`) and ESM (`esm`)
- Generate TypeScript declaration files (`.d.ts`)
- Bundle code but mark external dependencies (if any dev deps leak)
- Minify production builds

**FR-1.4**: The `tsconfig.json` MUST enable:
- `"strict": true` (full strictness checks)
- `"target": "ES2020"` (Node 16+ baseline)
- `"module": "ESM"` / `"moduleResolution": "bundler"`
- `"skipLibCheck": true` (for build speed)

**FR-1.5**: Runtime dependencies (`dependencies` in `package.json`) MUST be empty (zero deps).

**FR-1.6**: Dev dependencies MAY include:
- `typescript`, `tsup` (build)
- `vitest` (testing)
- `@types/node` (type hints only)

---

#### Core Module: `idempotency.ts`

**FR-2.1**: Implement `IdempotencyGuard` class with the following public API:

```typescript
interface IdempotencyOptions {
  ttlSeconds: number; // Replay cache TTL (default: 3600)
  hashAlgorithm?: 'SHA-256'; // Only SHA-256 for now
}

class IdempotencyGuard {
  constructor(options?: Partial<IdempotencyOptions>);
  
  /**
   * Generate a deterministic fingerprint from request payload.
   * Uses SHA-256 of canonicalized JSON (sorted keys).
   */
  async fingerprint(payload: Record<string, any>): Promise<string>;
  
  /**
   * Check if idempotency key exists in cache.
   * Returns cached response if found, null otherwise.
   */
  get(idempotencyKey: string): Promise<CachedResponse | null>;
  
  /**
   * Store response in cache with TTL.
   */
  set(idempotencyKey: string, response: any, statusCode: number): Promise<void>;
  
  /**
   * Clear cache (for testing).
   */
  clear(): void;
}

interface CachedResponse {
  statusCode: number;
  body: any;
  cachedAt: Date;
}
```

**FR-2.2**: The `fingerprint()` method MUST:
- Sort object keys recursively (deterministic serialization)
- Compute SHA-256 hash using Web Crypto API (`crypto.subtle.digest`)
- Return lowercase hex string (64 characters)

**FR-2.3**: The cache implementation MUST:
- Use in-memory `Map<string, CachedResponse & { expiresAt: number }>`
- Lazily evict expired entries on `get()`
- Provide `clear()` for testing

**FR-2.4**: Cryptographic operations MUST use Web Crypto API:
```typescript
// Node 16+/browser
const buffer = await crypto.subtle.digest('SHA-256', data);
```

**FR-2.5**: If Web Crypto is unavailable (Node <16), throw clear error:
```typescript
throw new Error('Web Crypto API not available. Requires Node 16+ or modern browser.');
```

---

#### Core Module: `rate-limiter.ts`

**FR-3.1**: Implement `TokenBucketRateLimiter` class with the following API:

```typescript
interface RateLimitConfig {
  capacity: number;        // Max tokens in bucket
  refillRate: number;      // Tokens per second
  keyPrefix?: string;      // Namespace for keys (default: 'ratelimit')
}

interface RateLimitResult {
  allowed: boolean;
  remaining: number;       // Tokens left after this request
  resetAt: Date;           // When bucket refills to capacity
  retryAfter?: number;     // Seconds to wait if rejected
}

class TokenBucketRateLimiter {
  constructor(config: RateLimitConfig);
  
  /**
   * Check if request is allowed under rate limit.
   * Consumes `tokens` from the bucket if allowed.
   */
  async check(key: string, tokens?: number): Promise<RateLimitResult>;
  
  /**
   * Reset bucket for a key (for testing).
   */
  reset(key: string): void;
}
```

**FR-3.2**: The token bucket algorithm MUST:
- Refill tokens linearly: `tokensToAdd = (now - lastRefill) * refillRate`
- Cap tokens at `capacity`
- Allow request if `currentTokens >= requested`
- Consume tokens atomically (in-memory lock-free via synchronous Map ops)

**FR-3.3**: State storage MUST use in-memory `Map<string, BucketState>`:
```typescript
interface BucketState {
  tokens: number;
  lastRefill: number; // timestamp ms
}
```

**FR-3.4**: The `retryAfter` calculation MUST:
- Return `Math.ceil((requestedTokens - currentTokens) / refillRate)` seconds
- Only be present when `allowed: false`

---

#### Core Module: `circuit-breaker.ts`

**FR-4.1**: Implement `ProviderCircuitBreaker` class with state machine:

```typescript
enum CircuitState {
  CLOSED = 'CLOSED',
  OPEN = 'OPEN',
  HALF_OPEN = 'HALF_OPEN',
}

interface CircuitBreakerConfig {
  failureThreshold: number;     // Failures before OPEN (default: 5)
  failureWindowMs: number;      // Window for counting failures (default: 60000)
  recoveryTimeoutMs: number;    // Time before HALF_OPEN attempt (default: 30000)
  successThreshold: number;     // Successes to close from HALF_OPEN (default: 2)
  halfOpenMaxRequests: number;  // Max concurrent in HALF_OPEN (default: 3)
}

interface CircuitStatus {
  state: CircuitState;
  failureCount: number;
  lastFailureAt?: Date;
  openedAt?: Date;
  halfOpenSuccessCount?: number;
}

class ProviderCircuitBreaker {
  constructor(providerName: string, config?: Partial<CircuitBreakerConfig>);
  
  /**
   * Get current circuit state.
   */
  getState(): CircuitState;
  
  /**
   * Execute function with circuit breaker protection.
   * Throws CircuitOpenError if circuit is OPEN.
   */
  async execute<T>(fn: () => Promise<T>): Promise<T>;
  
  /**
   * Record success (for manual control).
   */
  recordSuccess(): void;
  
  /**
   * Record failure (for manual control).
   */
  recordFailure(error: Error): void;
  
  /**
   * Get full status (for observability).
   */
  getStatus(): CircuitStatus;
  
  /**
   * Reset circuit (for testing).
   */
  reset(): void;
}

class CircuitOpenError extends Error {
  constructor(providerName: string, willRetryAt: Date);
}
```

**FR-4.2**: State transitions MUST follow:

```
CLOSED → OPEN: 
  - When `failureCount >= failureThreshold` within `failureWindowMs`
  
OPEN → HALF_OPEN:
  - Automatically after `recoveryTimeoutMs` elapsed
  
HALF_OPEN → CLOSED:
  - After `successThreshold` consecutive successes
  
HALF_OPEN → OPEN:
  - On first failure in HALF_OPEN
```

**FR-4.3**: The `execute()` method MUST:
- Throw `CircuitOpenError` immediately if state is OPEN
- Limit concurrent requests to `halfOpenMaxRequests` in HALF_OPEN state
- Automatically call `recordSuccess()` or `recordFailure()` based on result
- Propagate the original error if function throws

**FR-4.4**: Implement `AdapterScorer` class for EWMA health tracking:

```typescript
interface HealthSignals {
  availability: number;      // 0.0 (open) to 1.0 (closed)
  ewmaErrorRate: number;     // 0.0 (no errors) to 1.0 (all errors)
  ewmaLatencyMs: number;     // Exponentially weighted latency
}

class AdapterScorer {
  constructor(alpha?: number); // EWMA smoothing factor (default: 0.3)
  
  /**
   * Record health signal (latency, error status).
   */
  record(providerName: string, latencyMs: number, isError: boolean): void;
  
  /**
   * Get current health signals.
   */
  getHealth(providerName: string): HealthSignals;
  
  /**
   * Calculate composite score (0.0 to 1.0, higher = better).
   */
  calculateScore(
    providerName: string,
    weights?: { availability?: number; errorRate?: number; latency?: number }
  ): number;
}
```

**FR-4.5**: EWMA update formula MUST be:
```typescript
newValue = alpha * sample + (1 - alpha) * currentValue
```

---

#### Core Module: `trace.ts`

**FR-5.1**: Implement `ExecutionTraceBuilder` for 6-step gateway trace:

```typescript
enum TraceStep {
  AUTH = 'auth',
  RATE_LIMIT = 'rate_limit',
  IDEMPOTENCY = 'idempotency',
  ROUTING = 'routing',
  EXECUTION = 'execution',
  FINALIZE = 'finalize',
}

interface TraceEvent {
  step: TraceStep;
  timestamp: Date;
  durationMs?: number;
  metadata?: Record<string, any>;
  error?: { type: string; message: string };
}

class ExecutionTraceBuilder {
  constructor(requestId: string);
  
  /**
   * Start a trace step.
   */
  start(step: TraceStep, metadata?: Record<string, any>): void;
  
  /**
   * End current step (measures duration automatically).
   */
  end(step: TraceStep, metadata?: Record<string, any>): void;
  
  /**
   * Record error on current step.
   */
  error(step: TraceStep, error: Error, metadata?: Record<string, any>): void;
  
  /**
   * Get all trace events.
   */
  getEvents(): TraceEvent[];
  
  /**
   * Export trace as JSON string.
   */
  toJSON(): string;
  
  /**
   * Get total duration (first start → last end).
   */
  getTotalDurationMs(): number;
}
```

**FR-5.2**: Duration measurement MUST:
- Use `performance.now()` for sub-millisecond precision
- Store start time on `start(step)` call
- Calculate `durationMs = performance.now() - startTime` on `end(step)`

**FR-5.3**: The 6-step trace MUST capture (in order):
1. **AUTH**: API key validation, tenant lookup
2. **RATE_LIMIT**: RPM/TPM checks, token consumption
3. **IDEMPOTENCY**: Fingerprint generation, cache lookup
4. **ROUTING**: Provider selection, circuit breaker checks
5. **EXECUTION**: Provider API call, retry logic
6. **FINALIZE**: Usage persistence, response caching

---

#### Core Module: `client.ts`

**FR-6.1**: Implement `InfrGateControlPlane` orchestrator class:

```typescript
interface ControlPlaneConfig {
  idempotency?: Partial<IdempotencyOptions>;
  rateLimit?: RateLimitConfig;
  circuitBreaker?: Partial<CircuitBreakerConfig>;
  tracing?: boolean; // Enable trace builder (default: true)
}

class InfrGateControlPlane {
  readonly idempotency: IdempotencyGuard;
  readonly rateLimit: TokenBucketRateLimiter;
  readonly circuitBreaker: ProviderCircuitBreaker;
  readonly scorer: AdapterScorer;
  
  constructor(config?: ControlPlaneConfig);
  
  /**
   * Execute request through full control plane pipeline.
   * Returns trace and result.
   */
  async execute<T>(
    requestId: string,
    tenantKey: string,
    payload: Record<string, any>,
    handler: () => Promise<T>
  ): Promise<{ result: T; trace: ExecutionTraceBuilder }>;
}
```

**FR-6.2**: The `execute()` method MUST orchestrate:
1. Create `ExecutionTraceBuilder(requestId)`
2. Generate idempotency fingerprint (trace: IDEMPOTENCY)
3. Check rate limit for `tenantKey` (trace: RATE_LIMIT)
4. Check idempotency cache replay (trace: IDEMPOTENCY)
5. Execute `handler()` through circuit breaker (trace: EXECUTION)
6. Cache response in idempotency guard (trace: FINALIZE)
7. Return `{ result, trace }`

**FR-6.3**: Implement `InfrGateClient` OpenAI-compatible wrapper:

```typescript
interface InfrGateClientConfig {
  baseUrl: string;
  apiKey: string;
  controlPlane?: ControlPlaneConfig;
  fetch?: typeof fetch; // Injectable for testing
}

class InfrGateClient {
  constructor(config: InfrGateClientConfig);
  
  /**
   * OpenAI-compatible chat completions endpoint.
   */
  async createChatCompletion(params: {
    model: string;
    messages: Array<{ role: string; content: string }>;
    temperature?: number;
    maxTokens?: number;
    stream?: boolean;
    idempotencyKey?: string;
  }): Promise<ChatCompletionResponse>;
}

interface ChatCompletionResponse {
  id: string;
  model: string;
  choices: Array<{
    message: { role: string; content: string };
    finishReason: string;
  }>;
  usage: {
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
  };
}
```

**FR-6.4**: The `createChatCompletion()` method MUST:
- Use `controlPlane.execute()` to wrap HTTP request
- Pass `idempotencyKey` or auto-generate via `fingerprint()`
- Set `Authorization: Bearer ${apiKey}` header
- Set `Idempotency-Key` header if provided
- Parse JSON response or throw typed error

---

#### Package Exports (`index.ts`)

**FR-7.1**: The root `index.ts` MUST export all public APIs:

```typescript
// Core primitives
export { IdempotencyGuard, type IdempotencyOptions, type CachedResponse } from './idempotency.js';
export { TokenBucketRateLimiter, type RateLimitConfig, type RateLimitResult } from './rate-limiter.js';
export { 
  ProviderCircuitBreaker, 
  CircuitState, 
  CircuitOpenError,
  AdapterScorer,
  type CircuitBreakerConfig, 
  type CircuitStatus,
  type HealthSignals 
} from './circuit-breaker.js';
export { 
  ExecutionTraceBuilder, 
  TraceStep, 
  type TraceEvent 
} from './trace.js';

// Orchestration
export { 
  InfrGateControlPlane, 
  InfrGateClient,
  type ControlPlaneConfig,
  type InfrGateClientConfig,
  type ChatCompletionResponse
} from './client.js';
```

**FR-7.2**: Internal utilities (canonicalization, time helpers) MUST NOT be exported.

---

### Non-Functional Requirements

#### Performance

**NFR-1.1**: Idempotency fingerprinting MUST complete in <10ms for 10KB payloads on Node.js 20.

**NFR-1.2**: Rate limiter check MUST complete in <1ms (in-memory operations only).

**NFR-1.3**: Circuit breaker state checks MUST complete in <0.1ms (simple Map lookup).

**NFR-1.4**: Bundle size MUST be ≤50KB minified (tree-shakeable ESM).

#### Compatibility

**NFR-2.1**: Runtime MUST support:
- **Node.js**: 16.x, 18.x, 20.x, 22.x (LTS versions)
- **Browsers**: Chrome 90+, Firefox 88+, Safari 15+ (Web Crypto support)
- **Edge Runtimes**: Cloudflare Workers, Vercel Edge Functions, Deno

**NFR-2.2**: Build MUST produce dual packages:
- CommonJS for Node.js `require()`
- ESM for modern bundlers/Node.js `import`

#### Testing

**NFR-3.1**: Test suite MUST achieve ≥80% code coverage.

**NFR-3.2**: Tests MUST be runnable via `npm test` using Vitest.

**NFR-3.3**: Tests MUST cover:
- Idempotency: SHA-256 correctness, TTL expiration, cache hits/misses
- Rate Limiter: Token consumption, refill math, capacity capping
- Circuit Breaker: State transitions, EWMA updates, concurrent HALF_OPEN limits
- Trace Builder: Duration calculation, step ordering, JSON serialization
- Client: HTTP mocking, error handling, header propagation

#### Documentation

**NFR-4.1**: README.md MUST include:
- **Installation**: `npm install @infrgate/control-plane`
- **Quick Start**: 5-line example of `InfrGateClient`
- **Architecture Diagram**: Mermaid flowchart of control plane pipeline
- **API Reference**: TSDoc comments for all public classes/methods
- **Usage Examples**: 
  - Standalone idempotency guard
  - Rate limiting in Next.js API route
  - Circuit breaker with fetch wrapper
  - Full control plane orchestration
- **Migration Guide**: Mapping from Python SDK to TypeScript equivalents

**NFR-4.2**: All public APIs MUST have TSDoc comments:
```typescript
/**
 * Generate SHA-256 fingerprint of request payload.
 * Uses deterministic JSON canonicalization (sorted keys).
 * 
 * @param payload - Arbitrary JSON-serializable object
 * @returns Hex-encoded SHA-256 hash (64 chars lowercase)
 * 
 * @example
 * ```typescript
 * const guard = new IdempotencyGuard();
 * const hash = await guard.fingerprint({ model: 'gpt-4', prompt: 'Hello' });
 * console.log(hash); // "a3b5c8..."
 * ```
 */
async fingerprint(payload: Record<string, any>): Promise<string>;
```

---

### Implementation Constraints

#### Directory Structure

**IC-1.1**: The SDK MUST be located at `packages/sdk/` in the monorepo:

```
packages/sdk/
├── src/
│   ├── idempotency.ts
│   ├── rate-limiter.ts
│   ├── circuit-breaker.ts
│   ├── trace.ts
│   ├── client.ts
│   ├── index.ts
│   └── utils/
│       ├── canonicalize.ts    # JSON key sorting
│       └── crypto.ts           # Web Crypto wrapper
├── tests/
│   ├── idempotency.test.ts
│   ├── rate-limiter.test.ts
│   ├── circuit-breaker.test.ts
│   ├── trace.test.ts
│   └── client.test.ts
├── package.json
├── tsconfig.json
├── tsup.config.ts
├── vitest.config.ts
├── README.md
└── LICENSE
```

#### Build Verification

**IC-2.1**: Build verification MUST pass:
```bash
cd packages/sdk
npm install
npm run build
npm test
```

**IC-2.2**: Build artifacts MUST include:
- `dist/index.js` (CommonJS bundle)
- `dist/index.mjs` (ESM bundle)
- `dist/index.d.ts` (TypeScript declarations)
- `dist/*.map` (source maps)

**IC-2.3**: TypeScript compilation MUST produce 0 errors with `--strict` enabled.

---

## Acceptance Criteria

The TypeScript Control Plane SDK feature is complete when:

1. **AC-1**: Package builds successfully via `npm run build` with 0 TypeScript errors
2. **AC-2**: Test suite passes with ≥80% coverage via `npm test`
3. **AC-3**: All 5 core modules are implemented and exported from `index.ts`
4. **AC-4**: README.md includes installation, architecture diagram, and 3+ usage examples
5. **AC-5**: `package.json` declares zero runtime dependencies
6. **AC-6**: Dual CommonJS/ESM builds are generated in `dist/`
7. **AC-7**: All public APIs have TSDoc comments
8. **AC-8**: Example integration (Next.js API route) successfully uses `InfrGateClient`

---

## Out of Scope

The following are explicitly **NOT** included in this feature:

- **Redis Integration**: This SDK uses in-memory storage only. Redis backing can be added in v0.3.0.
- **Streaming Support**: The `InfrGateClient` does not handle SSE streaming in v0.2.0.
- **Webhook Delivery**: No webhook primitives (existing in Python worker).
- **Database Persistence**: No Postgres/SQLite integration for usage ledger.
- **Admin Client**: No tenant/API key management (see existing Python SDK `admin.py`).
- **Multi-Region Sync**: Circuit breaker state is local; no cross-region coordination.
- **Browser Polyfills**: No polyfills for older browsers lacking Web Crypto.

---

## Dependencies & References

### Internal Dependencies

- **Python Source**: `src/infrgate/services/*.py` (reference implementation)
- **Existing Python SDK**: `sdk/src/infrgate_sdk/` (client patterns)

### External Standards

- **Web Crypto API**: [W3C Spec](https://www.w3.org/TR/WebCryptoAPI/)
- **Node.js Crypto**: [Node.js Docs](https://nodejs.org/api/crypto.html)
- **OpenAI API**: [OpenAI Reference](https://platform.openai.com/docs/api-reference)

### Build Tools

- **tsup**: Zero-config TypeScript bundler (https://tsup.egoist.dev/)
- **Vitest**: Fast unit test framework (https://vitest.dev/)
- **TypeScript**: v5.3+ with strict mode

---

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Web Crypto unavailable in Node <16** | High | Low | Throw clear error, document Node 16+ requirement |
| **Floating-point precision in token bucket** | Medium | Medium | Use integer arithmetic (tokens as millitokens) |
| **Memory leaks in long-running processes** | High | Medium | Implement LRU eviction for caches, document TTL best practices |
| **EWMA cold-start bias** | Low | High | Initialize EWMA with neutral values (0.5 error rate, 1000ms latency) |
| **Bundle size bloat** | Medium | Low | Tree-shaking, measure with `bundlephobia.com` |

---

## Open Questions

1. **Q**: Should `IdempotencyGuard` support pluggable storage (localStorage, IndexedDB)?  
   **A**: Not in v0.2.0. Document as future enhancement.

2. **Q**: Should `TokenBucketRateLimiter` support distributed mode (Redis)?  
   **A**: Not in v0.2.0. Provide abstract interface for future Redis adapter.

3. **Q**: Should `InfrGateClient` support custom `fetch` implementation?  
   **A**: Yes. Accept `fetch?: typeof fetch` in config for testing/mocking.

4. **Q**: Should circuit breaker support custom state persistence?  
   **A**: Not in v0.2.0. Use in-memory only.

---

## Glossary

- **CJS**: CommonJS module format (`require()`/`module.exports`)
- **ESM**: ECMAScript Module format (`import`/`export`)
- **EWMA**: Exponentially Weighted Moving Average
- **TTL**: Time To Live (cache expiration)
- **TSDoc**: TypeScript documentation comments (`/** */`)
- **Web Crypto API**: Browser/Node.js standard for cryptographic operations
- **Token Bucket**: Rate limiting algorithm with refillable capacity
- **Circuit Breaker**: Fault tolerance pattern preventing cascading failures

---

## Appendix A: Python-to-TypeScript Mapping

| Python Module | TypeScript Module | Key Changes |
|---------------|-------------------|-------------|
| `services/idempotency.py` | `idempotency.ts` | Remove SQLAlchemy, use in-memory Map |
| `services/rate_limiter.py` | `rate-limiter.ts` | Remove Redis, use in-memory token bucket |
| `services/reliability.py` | `circuit-breaker.ts` | Extract circuit breaker + EWMA scorer |
| `services/scoring.py` | `circuit-breaker.ts` (AdapterScorer) | Merge health tracking into single module |
| `sdk/client.py` | `client.ts` | Replace httpx with native `fetch()` |

---

## Appendix B: Example Usage

### B.1 Standalone Idempotency

```typescript
import { IdempotencyGuard } from '@infrgate/control-plane';

const guard = new IdempotencyGuard({ ttlSeconds: 3600 });

const payload = { model: 'gpt-4', messages: [{ role: 'user', content: 'Hello' }] };
const key = await guard.fingerprint(payload);

const cached = await guard.get(key);
if (cached) {
  return cached.body; // Replay
}

const response = await callOpenAI(payload);
await guard.set(key, response, 200);
```

### B.2 Rate Limiting in Next.js API Route

```typescript
import { TokenBucketRateLimiter } from '@infrgate/control-plane';

const limiter = new TokenBucketRateLimiter({
  capacity: 60,    // 60 requests
  refillRate: 1,   // per second
});

export async function POST(req: Request) {
  const tenantId = req.headers.get('X-Tenant-ID');
  const result = await limiter.check(tenantId);
  
  if (!result.allowed) {
    return Response.json(
      { error: 'Rate limit exceeded' },
      { 
        status: 429,
        headers: { 'Retry-After': String(result.retryAfter) }
      }
    );
  }
  
  // Process request...
}
```

### B.3 Full Control Plane Orchestration

```typescript
import { InfrGateControlPlane } from '@infrgate/control-plane';

const cp = new InfrGateControlPlane({
  rateLimit: { capacity: 100, refillRate: 10 },
  circuitBreaker: { failureThreshold: 5 },
});

const { result, trace } = await cp.execute(
  'req-123',
  'tenant-abc',
  { model: 'gpt-4', prompt: 'Hello' },
  async () => {
    return await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      body: JSON.stringify({ model: 'gpt-4', messages: [...] }),
    });
  }
);

console.log('Total duration:', trace.getTotalDurationMs(), 'ms');
console.log('Steps:', trace.getEvents());
```

---

**End of Requirements Document**
