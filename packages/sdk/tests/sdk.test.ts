import { describe, it, expect, beforeEach } from 'vitest';
import {
  IdempotencyGuard,
  TokenBucketRateLimiter,
  ProviderCircuitBreaker,
  CircuitState,
  CircuitOpenError,
  AdapterScorer,
  ExecutionTraceBuilder,
  TraceStep,
  InfrGateControlPlane,
  InfrGateClient,
} from '../src/index.js';

describe('IdempotencyGuard', () => {
  let guard: IdempotencyGuard;

  beforeEach(() => {
    guard = new IdempotencyGuard({ ttlSeconds: 1 });
  });

  it('should generate deterministic fingerprints', async () => {
    const payload1 = { b: 2, a: 1, nested: { z: 3, y: 2 } };
    const payload2 = { a: 1, b: 2, nested: { y: 2, z: 3 } };

    const fp1 = await guard.fingerprint(payload1);
    const fp2 = await guard.fingerprint(payload2);

    expect(fp1).toBe(fp2);
    expect(fp1).toMatch(/^[a-f0-9]{64}$/); // Valid SHA-256 hex
  });

  it('should cache and retrieve responses', async () => {
    const key = 'test_key';
    const response = { message: 'Hello, world!' };

    await guard.set(key, response, 200);
    const cached = await guard.get(key);

    expect(cached).toBeTruthy();
    expect(cached?.statusCode).toBe(200);
    expect(cached?.body).toEqual(response);
    expect(cached?.cachedAt).toBeInstanceOf(Date);
  });

  it('should return null for cache miss', async () => {
    const cached = await guard.get('nonexistent_key');
    expect(cached).toBeNull();
  });

  it('should expire cached entries after TTL', async () => {
    const key = 'expire_test';
    await guard.set(key, { data: 'test' }, 200);

    // Wait for TTL to expire (1 second)
    await new Promise(resolve => setTimeout(resolve, 1100));

    const cached = await guard.get(key);
    expect(cached).toBeNull();
  });

  it('should clear all cache entries', async () => {
    await guard.set('key1', { data: 1 }, 200);
    await guard.set('key2', { data: 2 }, 200);

    guard.clear();

    expect(await guard.get('key1')).toBeNull();
    expect(await guard.get('key2')).toBeNull();
  });
});

describe('TokenBucketRateLimiter', () => {
  let limiter: TokenBucketRateLimiter;

  beforeEach(() => {
    limiter = new TokenBucketRateLimiter({
      capacity: 10,
      refillRate: 2, // 2 tokens per second
    });
  });

  it('should allow requests within capacity', async () => {
    const result = await limiter.check('user1', 5);
    
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(5);
  });

  it('should deny requests exceeding capacity', async () => {
    const result = await limiter.check('user1', 15);
    
    expect(result.allowed).toBe(false);
    expect(result.remaining).toBe(0);
    expect(result.retryAfter).toBeGreaterThan(0);
  });

  it('should consume tokens correctly', async () => {
    await limiter.check('user1', 3);
    await limiter.check('user1', 4);
    const result = await limiter.check('user1', 2);
    
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(1);
  });

  it('should refill tokens over time', async () => {
    // Consume all tokens
    await limiter.check('user1', 10);

    // Wait 1 second (should refill 2 tokens)
    await new Promise(resolve => setTimeout(resolve, 1000));

    const result = await limiter.check('user1', 2);
    expect(result.allowed).toBe(true);
  });

  it('should isolate buckets by key', async () => {
    await limiter.check('user1', 10);
    const result = await limiter.check('user2', 5);
    
    expect(result.allowed).toBe(true);
  });

  it('should reset bucket state', async () => {
    await limiter.check('user1', 10);
    limiter.reset('user1');
    
    const result = await limiter.check('user1', 10);
    expect(result.allowed).toBe(true);
  });
});

describe('ProviderCircuitBreaker', () => {
  let breaker: ProviderCircuitBreaker;

  beforeEach(() => {
    breaker = new ProviderCircuitBreaker('test-provider', {
      failureThreshold: 3,
      failureWindowMs: 10000,
      recoveryTimeoutMs: 1000,
      successThreshold: 2,
    });
  });

  it('should start in CLOSED state', () => {
    expect(breaker.getState()).toBe(CircuitState.CLOSED);
  });

  it('should execute successful requests', async () => {
    const result = await breaker.execute(async () => {
      return 'success';
    });
    
    expect(result).toBe('success');
  });

  it('should transition to OPEN after failure threshold', async () => {
    const failingFn = async () => {
      throw new Error('Provider error');
    };

    // Record 3 failures to trip circuit
    for (let i = 0; i < 3; i++) {
      try {
        await breaker.execute(failingFn);
      } catch {
        // Expected
      }
    }

    expect(breaker.getState()).toBe(CircuitState.OPEN);
  });

  it('should throw CircuitOpenError when OPEN', async () => {
    // Trip the circuit
    for (let i = 0; i < 3; i++) {
      try {
        await breaker.execute(async () => { throw new Error('fail'); });
      } catch {}
    }

    await expect(
      breaker.execute(async () => 'test')
    ).rejects.toThrow(CircuitOpenError);
  });

  it('should transition to HALF_OPEN after recovery timeout', async () => {
    // Trip the circuit
    for (let i = 0; i < 3; i++) {
      try {
        await breaker.execute(async () => { throw new Error('fail'); });
      } catch {}
    }

    expect(breaker.getState()).toBe(CircuitState.OPEN);

    // Wait for recovery timeout
    await new Promise(resolve => setTimeout(resolve, 1100));

    expect(breaker.getState()).toBe(CircuitState.HALF_OPEN);
  });

  it('should reset circuit state', () => {
    breaker.recordFailure(new Error('test'));
    breaker.recordFailure(new Error('test'));
    breaker.reset();
    
    expect(breaker.getState()).toBe(CircuitState.CLOSED);
    expect(breaker.getStatus().failureCount).toBe(0);
  });
});

describe('AdapterScorer', () => {
  let scorer: AdapterScorer;

  beforeEach(() => {
    scorer = new AdapterScorer(0.5); // alpha = 0.5 for faster convergence
  });

  it('should return cold-start defaults for new providers', () => {
    const health = scorer.getHealth('new-provider');
    
    expect(health.availability).toBe(1.0);
    expect(health.ewmaErrorRate).toBe(0.0);
    expect(health.ewmaLatencyMs).toBe(1000.0);
  });

  it('should update EWMA metrics on record', () => {
    scorer.record('provider1', 500, false); // 500ms, success
    const health = scorer.getHealth('provider1');
    
    expect(health.ewmaLatencyMs).toBeLessThan(1000);
    expect(health.ewmaErrorRate).toBe(0.0);
  });

  it('should track error rate', () => {
    scorer.record('provider1', 100, true);  // error
    scorer.record('provider1', 100, true);  // error
    scorer.record('provider1', 100, false); // success
    
    const health = scorer.getHealth('provider1');
    expect(health.ewmaErrorRate).toBeGreaterThan(0);
    expect(health.ewmaErrorRate).toBeLessThan(1);
  });

  it('should calculate composite scores', () => {
    scorer.record('fast-provider', 100, false);
    scorer.record('slow-provider', 2000, false);
    
    const fastScore = scorer.calculateScore('fast-provider');
    const slowScore = scorer.calculateScore('slow-provider');
    
    expect(fastScore).toBeGreaterThan(slowScore);
  });

  it('should update availability based on circuit state', () => {
    scorer.updateAvailability('provider1', CircuitState.OPEN);
    expect(scorer.getHealth('provider1').availability).toBe(0.0);
    
    scorer.updateAvailability('provider1', CircuitState.HALF_OPEN);
    expect(scorer.getHealth('provider1').availability).toBe(0.5);
    
    scorer.updateAvailability('provider1', CircuitState.CLOSED);
    expect(scorer.getHealth('provider1').availability).toBe(1.0);
  });
});

describe('ExecutionTraceBuilder', () => {
  let trace: ExecutionTraceBuilder;

  beforeEach(() => {
    trace = new ExecutionTraceBuilder('req_test_123');
  });

  it('should record step start and end', () => {
    trace.start(TraceStep.AUTH);
    trace.end(TraceStep.AUTH, { tenantId: 'test' });
    
    const events = trace.getEvents();
    expect(events).toHaveLength(1);
    expect(events[0].step).toBe(TraceStep.AUTH);
    expect(events[0].durationMs).toBeGreaterThanOrEqual(0);
    expect(events[0].metadata?.tenantId).toBe('test');
  });

  it('should record multiple steps', () => {
    trace.start(TraceStep.AUTH);
    trace.end(TraceStep.AUTH);
    
    trace.start(TraceStep.RATE_LIMIT);
    trace.end(TraceStep.RATE_LIMIT);
    
    trace.start(TraceStep.EXECUTION);
    trace.end(TraceStep.EXECUTION);
    
    expect(trace.getEvents()).toHaveLength(3);
  });

  it('should record errors', () => {
    trace.start(TraceStep.RATE_LIMIT);
    trace.error(TraceStep.RATE_LIMIT, new Error('Rate limit exceeded'));
    
    const events = trace.getEvents();
    expect(events[0].error).toBeTruthy();
    expect(events[0].error?.message).toBe('Rate limit exceeded');
  });

  it('should calculate total duration', () => {
    trace.start(TraceStep.AUTH);
    trace.end(TraceStep.AUTH);
    
    trace.start(TraceStep.EXECUTION);
    trace.end(TraceStep.EXECUTION);
    
    const duration = trace.getTotalDurationMs();
    expect(duration).toBeGreaterThanOrEqual(0);
  });

  it('should export as JSON', () => {
    trace.start(TraceStep.AUTH);
    trace.end(TraceStep.AUTH);
    
    const json = trace.toJSON();
    const parsed = JSON.parse(json);
    
    expect(parsed.requestId).toBe('req_test_123');
    expect(parsed.events).toHaveLength(1);
    expect(parsed.totalDurationMs).toBeGreaterThanOrEqual(0);
  });

  it('should generate step summary with status', () => {
    trace.start(TraceStep.AUTH);
    trace.end(TraceStep.AUTH);
    
    trace.start(TraceStep.RATE_LIMIT);
    trace.error(TraceStep.RATE_LIMIT, new Error('Failed'));
    
    const summary = trace.toSummary();
    expect(summary).toHaveLength(2);
    expect(summary[0].status).toBe('passed');
    expect(summary[1].status).toBe('failed');
  });
});

describe('InfrGateControlPlane', () => {
  let cp: InfrGateControlPlane;

  beforeEach(() => {
    cp = new InfrGateControlPlane({
      rateLimit: { capacity: 100, refillRate: 10 },
      circuitBreaker: { failureThreshold: 3 },
    });
  });

  it('should execute handler through pipeline', async () => {
    const { result, trace } = await cp.execute(
      'req_123',
      'tenant_abc',
      { test: 'payload' },
      async () => {
        return { data: 'success' };
      }
    );

    expect(result.data).toBe('success');
    expect(trace.getEvents().length).toBeGreaterThan(0);
  });

  it('should replay cached responses', async () => {
    const payload = { test: 'cache' };
    const handler = async () => ({ data: 'original' });

    // First call
    const { result: result1 } = await cp.execute('req_1', 'tenant_1', payload, handler);
    
    // Second call with same payload (should be cached)
    const { result: result2 } = await cp.execute('req_2', 'tenant_1', payload, handler);

    expect(result1).toEqual(result2);
  });

  it('should get or create circuit breakers per provider', () => {
    const breaker1 = cp.getCircuitBreaker('openai');
    const breaker2 = cp.getCircuitBreaker('openai');
    const breaker3 = cp.getCircuitBreaker('groq');

    expect(breaker1).toBe(breaker2); // Same instance
    expect(breaker1).not.toBe(breaker3); // Different providers
  });

  it('should rank providers by health score', () => {
    // Record some metrics
    cp.scorer.record('openai', 500, false);
    cp.scorer.record('groq', 100, false);
    cp.scorer.record('gemini', 1000, false);

    const ranked = cp.rankProviders(['openai', 'groq', 'gemini']);

    // Groq should be first (lowest latency)
    expect(ranked[0]).toBe('groq');
  });
});

describe('InfrGateClient', () => {
  it('should create client with config', () => {
    const client = new InfrGateClient({
      baseUrl: 'https://api.example.com',
      apiKey: 'sk_test_123',
    });

    expect(client).toBeInstanceOf(InfrGateClient);
  });

  it('should call createChatCompletion with mocked fetch', async () => {
    const mockResponse = {
      id: 'chatcmpl_123',
      model: 'gpt-4',
      choices: [
        {
          message: { role: 'assistant', content: 'Hello!' },
          finishReason: 'stop',
        },
      ],
      usage: { promptTokens: 10, completionTokens: 5, totalTokens: 15 },
    };

    const mockFetch = async () => ({
      ok: true,
      json: async () => mockResponse,
    }) as Response;

    const client = new InfrGateClient({
      baseUrl: 'https://api.example.com',
      apiKey: 'sk_test_123',
      fetch: mockFetch,
    });

    const response = await client.createChatCompletion({
      model: 'gpt-4',
      messages: [{ role: 'user', content: 'Hello' }],
    });

    expect(response.id).toBe('chatcmpl_123');
    expect(response.choices[0].message.content).toBe('Hello!');
  });
});
