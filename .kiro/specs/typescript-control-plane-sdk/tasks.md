# Tasks Document

**Feature Name:** `typescript-control-plane-sdk`  
**Status:** Not Started  
**Created:** 2024-01-XX  
**Last Updated:** 2024-01-XX

---

## Overview

This document breaks down the implementation of the TypeScript Control Plane SDK into executable tasks. The SDK provides zero-dependency control plane primitives (idempotency, rate limiting, circuit breaking, tracing) for TypeScript/JavaScript LLM applications.

**Implementation Location:** `packages/sdk/`

**Total Estimated Effort:** 3-5 days

---

## Task Breakdown

### 1. Project Setup & Configuration

#### 1.1 Initialize Package Structure
- [ ] Create `packages/sdk/` directory structure:
  - `src/` (source files)
  - `tests/` (test files)
  - `dist/` (build output, gitignored)
- [ ] Create `packages/sdk/.gitignore` with entries for:
  - `node_modules/`
  - `dist/`
  - `coverage/`
  - `*.log`

**Acceptance:** Directory structure exists and is tracked in git

---

#### 1.2 Configure package.json
- [ ] Create `packages/sdk/package.json` with:
  - Name: `@infrgate/control-plane`
  - Version: `0.2.0`
  - Type: `module` (ESM-first)
  - Main entry points: `main`, `module`, `types` fields
  - Exports map with conditional exports
  - Scripts: `build`, `test`, `test:watch`, `test:coverage`, `typecheck`
  - Engines: Node.js `>=16.0.0`
  - Keywords: llm, gateway, rate-limiting, circuit-breaker, idempotency, openai
  - **Zero runtime dependencies** (empty `dependencies` object)
- [ ] Add devDependencies:
  - `typescript@^5.3.3`
  - `tsup@^8.0.1`
  - `vitest@^1.1.0`
  - `@types/node@^20.10.0`

**Acceptance:** `package.json` is valid, `npm install` succeeds

---

#### 1.3 Configure TypeScript
- [ ] Create `packages/sdk/tsconfig.json` with:
  - `strict: true` (full strictness)
  - `target: "ES2020"` (Node 16+ baseline)
  - `module: "ESNext"`
  - `moduleResolution: "bundler"`
  - `lib: ["ES2020", "DOM"]` (for Web Crypto API)
  - `declaration: true` (generate .d.ts files)
  - `sourceMap: true`
  - `outDir: "./dist"`
  - `rootDir: "./src"`
  - Strict flags: `noUnusedLocals`, `noUnusedParameters`, `noImplicitReturns`, `noFallthroughCasesInSwitch`

**Acceptance:** `npm run typecheck` runs without errors (even with empty src/)

---

#### 1.4 Configure Build System (tsup)
- [ ] Create `packages/sdk/tsup.config.ts` with:
  - Entry point: `src/index.ts`
  - Formats: `['cjs', 'esm']`
  - Generate declaration files (`dts: true`)
  - Enable minification and tree-shaking
  - Output extensions: `.js` for CJS, `.mjs` for ESM
  - Source maps enabled
  - Clean dist on each build

**Acceptance:** `npm run build` creates `dist/index.js`, `dist/index.mjs`, `dist/index.d.ts`

---

#### 1.5 Configure Test Framework (Vitest)
- [ ] Create `packages/sdk/vitest.config.ts` with:
  - Environment: `node`
  - Coverage provider: `v8`
  - Coverage reporters: text, json, html
  - Coverage thresholds: 80% for lines, functions, branches, statements
  - Exclude: `node_modules/`, `dist/`, `tests/`, `**/*.test.ts`
- [ ] Add test script to run Vitest

**Acceptance:** `npm test` runs (even with no tests yet) and reports 0 tests

---

### 2. Core Utilities

#### 2.1 Implement JSON Canonicalization
- [ ] Create `packages/sdk/src/utils/canonicalize.ts` with:
  - `canonicalize(obj: any): string` function
  - Recursive object key sorting
  - Array handling (preserve order)
  - Primitive type handling (null, boolean, number, string)
  - Deterministic JSON serialization
- [ ] Add unit tests in `tests/utils/canonicalize.test.ts`:
  - Test order-independence: `{b: 2, a: 1}` === `{a: 1, b: 2}`
  - Test nested objects: `{user: {name: "Alice", id: 123}}`
  - Test arrays: preserve order `[1, 2, 3]` !== `[3, 2, 1]`
  - Test mixed types: objects with arrays, nulls, booleans
  - Test edge cases: empty objects, deeply nested structures

**Acceptance:** All canonicalize tests pass, deterministic hashing verified

---

#### 2.2 Implement Web Crypto Wrapper
- [ ] Create `packages/sdk/src/utils/crypto.ts` with:
  - `CryptoUnavailableError` class
  - `getSubtleCrypto(): SubtleCrypto` function
    - Check `globalThis.crypto.subtle` (Node 16+/browser)
    - Check `global.crypto.subtle` (Node 15.x fallback)
    - Throw clear error if unavailable
  - `sha256(input: string): Promise<string>` function
    - Use `crypto.subtle.digest('SHA-256', data)`
    - Convert result to lowercase hex string
  - `bufferToHex(buffer: ArrayBuffer): string` helper
- [ ] Add unit tests in `tests/utils/crypto.test.ts`:
  - Test SHA-256 output format (64-char hex string)
  - Test known SHA-256 vectors: `sha256('hello')` === `'2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'`
  - Test empty string: `sha256('')` !== error
  - Test Unicode handling
  - Mock unavailable crypto to test error path

**Acceptance:** All crypto tests pass, SHA-256 hashes are deterministic and correct

---

### 3. Idempotency Module

#### 3.1 Implement IdempotencyGuard Class
- [ ] Create `packages/sdk/src/idempotency.ts` with:
  - Export `IdempotencyOptions` interface:
    - `ttlSeconds: number` (default: 3600)
    - `hashAlgorithm?: 'SHA-256'`
  - Export `CachedResponse` interface:
    - `statusCode: number`
    - `body: any`
    - `cachedAt: Date`
  - Internal `CacheEntry` type:
    - `response: CachedResponse`
    - `expiresAt: number` (Unix timestamp ms)
  - `IdempotencyGuard` class:
    - Private `cache: Map<string, CacheEntry>`
    - Private `options: IdempotencyOptions`
    - Constructor with optional config
    - `async fingerprint(payload: Record<string, any>): Promise<string>` - use canonicalize + sha256
    - `async get(key: string): Promise<CachedResponse | null>` - check cache, lazy evict expired
    - `async set(key: string, response: any, statusCode: number): Promise<void>` - store with TTL
    - `clear(): void` - clear cache for testing

**Acceptance:** Class compiles with no TypeScript errors, all methods implemented

---

#### 3.2 Test IdempotencyGuard
- [ ] Create `tests/idempotency.test.ts` with test cases:
  - **Fingerprinting:**
    - Test order-independent hashing: `fingerprint({b: 2, a: 1})` === `fingerprint({a: 1, b: 2})`
    - Test nested object determinism
    - Test SHA-256 output format (64-char hex)
  - **Cache operations:**
    - Test cache miss returns null
    - Test cache hit returns stored response
    - Test cache stores status code and body correctly
    - Test `cachedAt` timestamp is set
  - **TTL expiration:**
    - Test expired entries return null on `get()`
    - Test non-expired entries are preserved
    - Mock `Date.now()` to control time
  - **Clear operation:**
    - Test `clear()` empties cache completely

**Acceptance:** All idempotency tests pass, ≥80% coverage for module

---

### 4. Rate Limiter Module

#### 4.1 Implement TokenBucketRateLimiter Class
- [ ] Create `packages/sdk/src/rate-limiter.ts` with:
  - Export `RateLimitConfig` interface:
    - `capacity: number` (max tokens)
    - `refillRate: number` (tokens per second)
    - `keyPrefix?: string` (default: 'ratelimit')
  - Export `RateLimitResult` interface:
    - `allowed: boolean`
    - `remaining: number`
    - `resetAt: Date`
    - `retryAfter?: number` (seconds, only if denied)
  - Internal `BucketState` interface:
    - `tokens: number`
    - `lastRefill: number` (timestamp ms)
  - `TokenBucketRateLimiter` class:
    - Private `config: RateLimitConfig`
    - Private `buckets: Map<string, BucketState>`
    - Constructor
    - `async check(key: string, tokens?: number): Promise<RateLimitResult>`
      - Refill tokens: `tokensToAdd = (now - lastRefill) * refillRate / 1000`
      - Cap at capacity
      - Allow if `currentTokens >= requested`
      - Calculate `retryAfter` if denied: `Math.ceil((deficit) / refillRate)`
    - `reset(key: string): void` - delete bucket state
    - Private `refill(state: BucketState, now: number): BucketState` - refill logic

**Acceptance:** Class compiles, token bucket algorithm correctly implemented

---

#### 4.2 Test TokenBucketRateLimiter
- [ ] Create `tests/rate-limiter.test.ts` with test cases:
  - **Token consumption:**
    - Test cold start: full capacity available
    - Test token consumption: `check(key, 10)` reduces remaining by 10
    - Test sequential requests: tokens decrease correctly
  - **Refill mechanism:**
    - Test tokens refill over time (mock `Date.now()`)
    - Test capacity cap: refill doesn't exceed capacity
    - Test partial refill: 2 seconds elapsed = 2 * refillRate tokens added
  - **Rate limit denial:**
    - Test request denied when tokens insufficient
    - Test `retryAfter` calculation: deficit / refillRate
    - Test `allowed: false` and `remaining: 0`
  - **Per-key isolation:**
    - Test different keys have independent buckets
    - Test key prefix namespacing
  - **Reset operation:**
    - Test `reset(key)` clears bucket state

**Acceptance:** All rate limiter tests pass, ≥80% coverage

---

### 5. Circuit Breaker Module

#### 5.1 Implement Circuit Breaker State Machine
- [ ] Create `packages/sdk/src/circuit-breaker.ts` with:
  - Export `CircuitState` enum:
    - `CLOSED`, `OPEN`, `HALF_OPEN`
  - Export `CircuitBreakerConfig` interface:
    - `failureThreshold: number` (default: 5)
    - `failureWindowMs: number` (default: 60000)
    - `recoveryTimeoutMs: number` (default: 30000)
    - `successThreshold: number` (default: 2)
    - `halfOpenMaxRequests: number` (default: 3)
  - Export `CircuitStatus` interface:
    - `state: CircuitState`
    - `failureCount: number`
    - `lastFailureAt?: Date`
    - `openedAt?: Date`
    - `halfOpenSuccessCount?: number`
  - Internal `FailureWindow` interface:
    - `timestamp: number`
    - `error: string`

**Acceptance:** Types and interfaces compile correctly

---

#### 5.2 Implement ProviderCircuitBreaker Class
- [ ] Continue in `packages/sdk/src/circuit-breaker.ts`:
  - `ProviderCircuitBreaker` class:
    - Private `providerName: string`
    - Private `config: CircuitBreakerConfig`
    - Private `state: CircuitState` (initialize CLOSED)
    - Private `failures: FailureWindow[]` (sliding window)
    - Private `halfOpenCount: number` (concurrent requests in HALF_OPEN)
    - Private `openedAt?: number` (timestamp when opened)
    - Private `halfOpenSuccessCount: number`
    - Constructor
    - `getState(): CircuitState`
    - `async execute<T>(fn: () => Promise<T>): Promise<T>`
      - Throw `CircuitOpenError` if OPEN (check recovery timeout first)
      - Limit concurrent requests in HALF_OPEN
      - Execute function
      - Call `recordSuccess()` or `recordFailure()` based on result
      - Handle state transitions
    - `recordSuccess(): void` - update HALF_OPEN → CLOSED logic
    - `recordFailure(error: Error): void` - update CLOSED → OPEN, HALF_OPEN → OPEN
    - `getStatus(): CircuitStatus`
    - `reset(): void` - reset to CLOSED state
    - Private `transition(newState: CircuitState): void`
    - Private `shouldOpen(): boolean` - check failure count in window
    - Private `canAttemptHalfOpen(): boolean` - check recovery timeout

**Acceptance:** Circuit breaker class compiles, state machine logic implemented

---

#### 5.3 Implement CircuitOpenError Class
- [ ] Continue in `packages/sdk/src/circuit-breaker.ts`:
  - Export `CircuitOpenError extends Error`:
    - `providerName: string`
    - `willRetryAt: Date`
    - Constructor with descriptive message

**Acceptance:** Error class compiles and extends Error correctly

---

#### 5.4 Implement AdapterScorer Class
- [ ] Continue in `packages/sdk/src/circuit-breaker.ts`:
  - Export `HealthSignals` interface:
    - `availability: number` (0.0 to 1.0)
    - `ewmaErrorRate: number` (0.0 to 1.0)
    - `ewmaLatencyMs: number`
  - `AdapterScorer` class:
    - Private `alpha: number` (default: 0.3)
    - Private `healthMetrics: Map<string, HealthSignals>`
    - Constructor
    - `record(providerName: string, latencyMs: number, isError: boolean): void`
      - Update EWMA: `newValue = alpha * sample + (1 - alpha) * currentValue`
      - Update availability based on circuit breaker state
    - `getHealth(providerName: string): HealthSignals`
      - Return current metrics or cold-start defaults
    - `calculateScore(providerName: string, weights?: {...}): number`
      - Composite score: weighted sum of availability, error rate, latency
      - Normalize latency to 0-1 scale
      - Return clamped score [0, 1]

**Acceptance:** Scorer class compiles, EWMA calculations implemented

---

#### 5.5 Test Circuit Breaker
- [ ] Create `tests/circuit-breaker.test.ts` with test cases:
  - **State transitions:**
    - Test CLOSED → OPEN: after `failureThreshold` failures
    - Test OPEN → HALF_OPEN: after `recoveryTimeoutMs` elapsed
    - Test HALF_OPEN → CLOSED: after `successThreshold` successes
    - Test HALF_OPEN → OPEN: on first failure
  - **Failure window:**
    - Test sliding window: old failures expire
    - Test failure count resets outside window
  - **Execute method:**
    - Test successful execution in CLOSED state
    - Test `CircuitOpenError` thrown in OPEN state
    - Test concurrent request limit in HALF_OPEN
    - Test automatic `recordSuccess()`/`recordFailure()` calls
  - **AdapterScorer:**
    - Test EWMA update formula correctness
    - Test cold-start defaults (availability: 1.0, errorRate: 0.0)
    - Test composite score calculation
    - Test score clamping [0, 1]
  - **Reset operation:**
    - Test circuit reset to CLOSED state

**Acceptance:** All circuit breaker tests pass, ≥80% coverage

---

### 6. Trace Module

#### 6.1 Implement ExecutionTraceBuilder Class
- [ ] Create `packages/sdk/src/trace.ts` with:
  - Export `TraceStep` enum:
    - `AUTH`, `RATE_LIMIT`, `IDEMPOTENCY`, `ROUTING`, `EXECUTION`, `FINALIZE`
  - Export `TraceEvent` interface:
    - `step: TraceStep`
    - `timestamp: Date`
    - `durationMs?: number`
    - `metadata?: Record<string, any>`
    - `error?: { type: string; message: string }`
  - `ExecutionTraceBuilder` class:
    - Private `requestId: string`
    - Private `events: TraceEvent[]`
    - Private `stepStartTimes: Map<TraceStep, number>` (use `performance.now()`)
    - Constructor
    - `start(step: TraceStep, metadata?: Record<string, any>): void`
      - Record start time with `performance.now()`
      - Push event to events array
    - `end(step: TraceStep, metadata?: Record<string, any>): void`
      - Calculate duration: `performance.now() - startTime`
      - Find corresponding start event and add `durationMs`
      - Merge metadata
    - `error(step: TraceStep, error: Error, metadata?: Record<string, any>): void`
      - Find event, add error info
    - `getEvents(): TraceEvent[]`
    - `toJSON(): string` - serialize to JSON
    - `getTotalDurationMs(): number` - first start to last end

**Acceptance:** Trace builder class compiles, timing mechanism implemented

---

#### 6.2 Test ExecutionTraceBuilder
- [ ] Create `tests/trace.test.ts` with test cases:
  - **Event recording:**
    - Test `start()` records timestamp and metadata
    - Test `end()` calculates duration correctly
    - Test `error()` adds error info to event
  - **Timing precision:**
    - Test `performance.now()` usage (sub-millisecond precision)
    - Test duration calculation accuracy
  - **Event ordering:**
    - Test events maintain insertion order
    - Test multiple steps recorded sequentially
  - **Total duration:**
    - Test `getTotalDurationMs()` spans full trace
  - **JSON serialization:**
    - Test `toJSON()` produces valid JSON
    - Test Date serialization to ISO 8601
  - **Edge cases:**
    - Test `end()` without `start()` throws error
    - Test multiple `start()` calls for same step (last wins)

**Acceptance:** All trace tests pass, ≥80% coverage

---

### 7. Client Module

#### 7.1 Implement InfrGateControlPlane Class
- [ ] Create `packages/sdk/src/client.ts` with:
  - Export `ControlPlaneConfig` interface:
    - `idempotency?: Partial<IdempotencyOptions>`
    - `rateLimit?: RateLimitConfig`
    - `circuitBreaker?: Partial<CircuitBreakerConfig>`
    - `tracing?: boolean` (default: true)
  - Export `ExecutionResult<T>` interface:
    - `result: T`
    - `trace: ExecutionTraceBuilder`
  - `InfrGateControlPlane` class:
    - Public readonly `idempotency: IdempotencyGuard`
    - Public readonly `rateLimit: TokenBucketRateLimiter`
    - Public readonly `circuitBreaker: ProviderCircuitBreaker`
    - Public readonly `scorer: AdapterScorer`
    - Private `config: ControlPlaneConfig`
    - Constructor (initialize all components)
    - `async execute<T>(requestId, tenantKey, payload, handler): Promise<ExecutionResult<T>>`
      - Create trace builder
      - Step 1: Generate idempotency fingerprint (trace: IDEMPOTENCY)
      - Step 2: Check idempotency cache (early return if hit)
      - Step 3: Check rate limit (trace: RATE_LIMIT, throw if denied)
      - Step 4: Execute handler through circuit breaker (trace: EXECUTION)
      - Step 5: Cache response (trace: FINALIZE)
      - Return result + trace

**Acceptance:** Control plane orchestration class compiles, pipeline implemented

---

#### 7.2 Implement InfrGateClient Class
- [ ] Continue in `packages/sdk/src/client.ts`:
  - Export `InfrGateClientConfig` interface:
    - `baseUrl: string`
    - `apiKey: string`
    - `controlPlane?: ControlPlaneConfig`
    - `fetch?: typeof fetch` (injectable for testing)
  - Export `ChatCompletionResponse` interface (OpenAI-compatible):
    - `id: string`
    - `model: string`
    - `choices: Array<{ message: { role: string; content: string }; finishReason: string }>`
    - `usage: { promptTokens: number; completionTokens: number; totalTokens: number }`
  - `InfrGateClient` class:
    - Private `config: InfrGateClientConfig`
    - Private `controlPlane: InfrGateControlPlane`
    - Constructor (initialize control plane)
    - `async createChatCompletion(params): Promise<ChatCompletionResponse>`
      - Generate requestId: `crypto.randomUUID()`
      - Build payload from params
      - Use `controlPlane.execute()` to wrap request
      - Make HTTP request with fetch (use injected or global)
      - Set headers: `Authorization`, `Content-Type`, `Idempotency-Key` (if provided)
      - Parse JSON response
      - Handle errors (throw typed errors)

**Acceptance:** Client class compiles, OpenAI-compatible API implemented

---

#### 7.3 Test Client Module
- [ ] Create `tests/client.test.ts` with test cases:
  - **Control plane orchestration:**
    - Test full pipeline: idempotency → rate limit → circuit breaker → execution
    - Test idempotency cache hit (early return)
    - Test rate limit denial (throw error)
    - Test circuit breaker open (throw error)
    - Test successful execution with trace
  - **InfrGateClient:**
    - Mock `fetch` to test HTTP requests
    - Test request headers: Authorization, Content-Type
    - Test idempotency key header propagation
    - Test successful response parsing
    - Test error handling (4xx, 5xx responses)
    - Test requestId generation (UUID format)
  - **Integration:**
    - Test end-to-end: client → control plane → mocked provider → response
    - Test trace contains all 6 steps
    - Test metadata propagation

**Acceptance:** All client tests pass, ≥80% coverage

---

### 8. Root Exports

#### 8.1 Implement index.ts
- [ ] Create `packages/sdk/src/index.ts` with:
  - Export from `idempotency.ts`:
    - `IdempotencyGuard`
    - `type IdempotencyOptions`
    - `type CachedResponse`
  - Export from `rate-limiter.ts`:
    - `TokenBucketRateLimiter`
    - `type RateLimitConfig`
    - `type RateLimitResult`
  - Export from `circuit-breaker.ts`:
    - `ProviderCircuitBreaker`
    - `CircuitState`
    - `CircuitOpenError`
    - `AdapterScorer`
    - `type CircuitBreakerConfig`
    - `type CircuitStatus`
    - `type HealthSignals`
  - Export from `trace.ts`:
    - `ExecutionTraceBuilder`
    - `TraceStep`
    - `type TraceEvent`
  - Export from `client.ts`:
    - `InfrGateControlPlane`
    - `InfrGateClient`
    - `type ControlPlaneConfig`
    - `type InfrGateClientConfig`
    - `type ChatCompletionResponse`
    - `type ExecutionResult`
- [ ] Verify all exports use correct ESM syntax (`.js` extension for local imports)

**Acceptance:** `index.ts` compiles, all public APIs exported, no internal utils exposed

---

#### 8.2 Verify Build Output
- [ ] Run `npm run build` and verify:
  - `dist/index.js` exists (CommonJS bundle, minified)
  - `dist/index.mjs` exists (ESM bundle, minified)
  - `dist/index.d.ts` exists (TypeScript declarations)
  - Source maps generated: `.map` files
- [ ] Test tree-shaking: import single module and verify bundle size
- [ ] Test dual package:
  - Create CJS test: `const { IdempotencyGuard } = require('@infrgate/control-plane')`
  - Create ESM test: `import { IdempotencyGuard } from '@infrgate/control-plane'`

**Acceptance:** Build produces valid dual packages, tree-shaking works

---

### 9. Documentation

#### 9.1 Write README.md
- [ ] Create `packages/sdk/README.md` with sections:
  - **Header:**
    - Package name and badges (build status, coverage, npm version)
    - One-line description: "Zero-dependency TypeScript SDK for InfrGate control plane primitives"
  - **Features:**
    - Bullet list of 5 core modules
    - Zero dependencies, tree-shakeable, type-safe
  - **Installation:**
    - `npm install @infrgate/control-plane`
    - Node.js version requirement (≥16.0.0)
  - **Quick Start:**
    - 5-line example using `InfrGateClient`
  - **Architecture Diagram:**
    - Mermaid flowchart of control plane pipeline (copy from design.md)

**Acceptance:** README header, features, installation, quick start complete

---

#### 9.2 Add API Documentation
- [ ] Continue `packages/sdk/README.md`:
  - **API Reference:**
    - Section for each module (IdempotencyGuard, RateLimiter, CircuitBreaker, Trace, Client)
    - Method signatures with parameter descriptions
    - Example usage for each class
  - **Usage Examples:**
    - Example 1: Standalone idempotency guard
    - Example 2: Rate limiting in Next.js API route
    - Example 3: Circuit breaker with fetch wrapper
    - Example 4: Full control plane orchestration
    - Example 5: OpenAI-compatible client integration

**Acceptance:** API reference and 5 usage examples complete

---

#### 9.3 Add Migration Guide
- [ ] Continue `packages/sdk/README.md`:
  - **Migration from Python SDK:**
    - Table mapping Python modules to TypeScript equivalents
    - Key differences (in-memory vs Redis, Web Crypto vs hashlib)
    - Code comparison examples
  - **Configuration:**
    - Environment variables (if any)
    - Configuration object structures
  - **Troubleshooting:**
    - Common errors (Web Crypto unavailable, etc.)
    - Solutions and workarounds

**Acceptance:** Migration guide complete with examples

---

#### 9.4 Add TSDoc Comments
- [ ] Add TSDoc comments to all public APIs:
  - **Class descriptions:** Purpose and usage overview
  - **Method descriptions:** What the method does, when to use it
  - **Parameters:** Type and description for each param
  - **Returns:** Return type and description
  - **Examples:** Code snippet showing usage
  - **Throws:** Document errors that can be thrown
- [ ] Ensure TSDoc format is correct for documentation generators

**Acceptance:** All public APIs have comprehensive TSDoc comments

---

### 10. Testing & Quality Assurance

#### 10.1 Achieve Test Coverage Target
- [ ] Run `npm run test:coverage` and verify:
  - Lines: ≥80%
  - Functions: ≥80%
  - Branches: ≥80%
  - Statements: ≥80%
- [ ] Add tests for any uncovered code paths
- [ ] Review coverage report HTML output

**Acceptance:** All coverage thresholds met (≥80%)

---

#### 10.2 Add Integration Tests
- [ ] Create `tests/integration.test.ts` with end-to-end scenarios:
  - Test full request flow: client → all control plane steps → response
  - Test idempotency replay: second request returns cached result
  - Test rate limit enforcement: burst traffic gets rejected
  - Test circuit breaker trip: multiple failures open circuit
  - Test EWMA health updates: scorer tracks latency and errors
  - Mock HTTP fetch for provider calls

**Acceptance:** Integration tests pass, cover realistic usage patterns

---

#### 10.3 Test Cross-Platform Compatibility
- [ ] Test in Node.js environments:
  - Node 16.x (minimum supported)
  - Node 18.x (LTS)
  - Node 20.x (LTS)
  - Node 22.x (latest)
- [ ] Test import methods:
  - CommonJS: `require()`
  - ESM: `import`
- [ ] Document any platform-specific behaviors

**Acceptance:** SDK works in all supported Node.js versions

---

#### 10.4 Measure Bundle Size
- [ ] Build production bundle: `npm run build`
- [ ] Measure minified sizes:
  - Full bundle size (all modules)
  - Individual module sizes (tree-shaking test)
- [ ] Verify bundle size ≤50KB minified
- [ ] Document bundle size in README

**Acceptance:** Bundle size ≤50KB, documented in README

---

### 11. Build Verification

#### 11.1 Run Full Build Pipeline
- [ ] Clean workspace: `rm -rf dist node_modules`
- [ ] Fresh install: `npm install`
- [ ] Type check: `npm run typecheck` (0 errors)
- [ ] Build: `npm run build` (succeeds)
- [ ] Test: `npm test` (all pass)
- [ ] Coverage: `npm run test:coverage` (≥80%)

**Acceptance:** Full build pipeline executes without errors

---

#### 11.2 Test Package Locally
- [ ] Pack package: `npm pack`
- [ ] Create test project: `mkdir test-install && cd test-install && npm init -y`
- [ ] Install packed tarball: `npm install ../infrgate-control-plane-0.2.0.tgz`
- [ ] Create test script importing SDK
- [ ] Run test script successfully (both CJS and ESM)

**Acceptance:** Packed package installs and works in external project

---

#### 11.3 Validate TypeScript Declarations
- [ ] Create TypeScript consumer project
- [ ] Import SDK and verify:
  - IntelliSense shows all exported types
  - Type checking works correctly
  - No missing type errors
  - Generics work as expected
- [ ] Test `moduleResolution: "node16"` compatibility

**Acceptance:** TypeScript declarations are complete and correct

---

### 12. Final Deliverables

#### 12.1 Create LICENSE File
- [ ] Create `packages/sdk/LICENSE` with MIT license text
- [ ] Update copyright year and owner (InfrGate Team)

**Acceptance:** LICENSE file exists

---

#### 12.2 Add Example Integration
- [ ] Create `packages/sdk/examples/` directory
- [ ] Add `examples/nextjs-api-route.ts`:
  - Next.js API route using InfrGateClient
  - Show rate limiting in action
  - Show error handling
- [ ] Add `examples/standalone-idempotency.ts`:
  - Minimal idempotency guard usage
- [ ] Add `examples/circuit-breaker-fetch.ts`:
  - Circuit breaker wrapping external API

**Acceptance:** 3 example files created, documented in README

---

#### 12.3 Prepare Package for Publishing
- [ ] Review `package.json` for publish readiness:
  - Version: `0.2.0`
  - Repository URL correct
  - Keywords comprehensive
  - Files whitelist correct (`dist`, `README.md`, `LICENSE`)
- [ ] Add `.npmignore` if needed
- [ ] Verify `prepublishOnly` script runs build + tests

**Acceptance:** Package ready for `npm publish` (do not publish yet)

---

#### 12.4 Final Documentation Review
- [ ] Review README.md for:
  - Typos and grammar
  - Broken links
  - Code examples work
  - Architecture diagram renders correctly
- [ ] Ensure all acceptance criteria from requirements.md are met
- [ ] Verify migration guide is accurate

**Acceptance:** Documentation is polished and complete

---

## Verification Checklist

Before marking the feature complete, verify:

- [ ] **AC-1:** Package builds successfully via `npm run build` with 0 TypeScript errors
- [ ] **AC-2:** Test suite passes with ≥80% coverage via `npm test`
- [ ] **AC-3:** All 5 core modules implemented and exported from `index.ts`
- [ ] **AC-4:** README.md includes installation, architecture diagram, and 3+ usage examples
- [ ] **AC-5:** `package.json` declares zero runtime dependencies
- [ ] **AC-6:** Dual CommonJS/ESM builds generated in `dist/`
- [ ] **AC-7:** All public APIs have TSDoc comments
- [ ] **AC-8:** Example integration (Next.js API route) successfully uses `InfrGateClient`

---

## Dependencies

- **Blocked By:** None (greenfield implementation)
- **Blocks:** 
  - TypeScript SDK documentation site
  - TypeScript SDK npm publishing
  - Next.js example application using SDK

---

## Notes

- **Zero Dependencies:** Maintain strict zero-dependency policy for runtime. Dev dependencies are acceptable.
- **Web Crypto Requirement:** Document Node.js 16+ requirement clearly in README and error messages.
- **In-Memory Storage:** All storage is in-memory in v0.2.0. Redis integration planned for v0.3.0.
- **Tree-Shaking:** Test with bundlers (Webpack, Rollup, esbuild) to verify ESM tree-shaking works.
- **Testing Strategy:** Use Vitest for fast, modern testing. Mock `fetch` and `crypto` where needed.
- **Performance:** Run basic benchmarks for fingerprinting (target: <10ms for 10KB payloads).

---

## Task Status Legend

- `[ ]` Not started
- `[~]` In progress
- `[x]` Complete
- `[!]` Blocked

---

**End of Tasks Document**
