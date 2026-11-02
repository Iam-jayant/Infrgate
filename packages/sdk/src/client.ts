import { IdempotencyGuard, type IdempotencyOptions } from './idempotency.js';
import { TokenBucketRateLimiter, type RateLimitConfig } from './rate-limiter.js';
import {
  ProviderCircuitBreaker,
  AdapterScorer,
  type CircuitBreakerConfig,
} from './circuit-breaker.js';
import { ExecutionTraceBuilder, TraceStep } from './trace.js';

/**
 * Configuration for the InfrGate control plane.
 */
export interface ControlPlaneConfig {
  /** Idempotency guard configuration */
  idempotency?: Partial<IdempotencyOptions>;
  /** Rate limiter configuration */
  rateLimit?: RateLimitConfig;
  /** Circuit breaker configuration (applied to all providers) */
  circuitBreaker?: Partial<CircuitBreakerConfig>;
  /** Enable execution tracing (default: true) */
  tracing?: boolean;
}

/**
 * Result of control plane execution.
 */
export interface ExecutionResult<T> {
  /** The result returned by the handler */
  result: T;
  /** Execution trace with timing and metadata */
  trace: ExecutionTraceBuilder;
}

/**
 * InfrGateControlPlane orchestrates the complete control plane pipeline.
 * Combines idempotency, rate limiting, circuit breaking, and tracing into a unified flow.
 * 
 * FAST-TRACK ADJUSTMENT: Supports per-provider circuit breakers and intelligent ranking
 * for multi-provider routing scenarios (e.g., OpenRouter → Groq → Gemini).
 * 
 * @example
 * ```typescript
 * const cp = new InfrGateControlPlane({
 *   rateLimit: { capacity: 100, refillRate: 10 },
 *   circuitBreaker: { failureThreshold: 5 },
 * });
 * 
 * const { result, trace } = await cp.execute(
 *   'req_123',
 *   'tenant_abc',
 *   { model: 'gpt-4', prompt: 'Hello' },
 *   async () => {
 *     return await fetch('https://api.openai.com/v1/chat/completions', {
 *       method: 'POST',
 *       body: JSON.stringify({ model: 'gpt-4', messages: [...] }),
 *     });
 *   }
 * );
 * 
 * console.log('Total duration:', trace.getTotalDurationMs(), 'ms');
 * ```
 */
export class InfrGateControlPlane {
  /** Public idempotency guard instance */
  public readonly idempotency: IdempotencyGuard;
  /** Public rate limiter instance */
  public readonly rateLimit: TokenBucketRateLimiter;
  /** Public adapter scorer for health tracking (FAST-TRACK) */
  public readonly scorer: AdapterScorer;

  private config: ControlPlaneConfig;
  private circuitBreakers: Map<string, ProviderCircuitBreaker> = new Map();

  /**
   * Create a new InfrGate control plane.
   * 
   * @param config - Control plane configuration
   */
  constructor(config?: ControlPlaneConfig) {
    this.config = {
      tracing: true,
      ...config,
    };

    this.idempotency = new IdempotencyGuard(config?.idempotency);
    this.rateLimit = new TokenBucketRateLimiter(
      config?.rateLimit || { capacity: 100, refillRate: 10 }
    );
    this.scorer = new AdapterScorer();
  }

  /**
   * Get or create a circuit breaker for a specific provider (FAST-TRACK ADJUSTMENT).
   * Enables per-provider fault isolation and health tracking.
   * 
   * @param providerName - Name of the provider (e.g., 'openai', 'groq', 'gemini')
   * @returns Circuit breaker instance for the provider
   * 
   * @example
   * ```typescript
   * const openaiBreaker = cp.getCircuitBreaker('openai');
   * const groqBreaker = cp.getCircuitBreaker('groq');
   * 
   * // Each provider has independent circuit state
   * console.log(openaiBreaker.getState()); // CLOSED
   * console.log(groqBreaker.getState());   // OPEN (if unhealthy)
   * ```
   */
  getCircuitBreaker(providerName: string): ProviderCircuitBreaker {
    let breaker = this.circuitBreakers.get(providerName);
    
    if (!breaker) {
      breaker = new ProviderCircuitBreaker(providerName, this.config.circuitBreaker);
      this.circuitBreakers.set(providerName, breaker);
    }

    // Update scorer availability based on circuit state
    this.scorer.updateAvailability(providerName, breaker.getState());

    return breaker;
  }

  /**
   * Rank providers by health score for intelligent routing (FAST-TRACK ADJUSTMENT).
   * Uses composite scoring from AdapterScorer and circuit breaker availability.
   * 
   * @param providers - Array of provider names to rank
   * @returns Providers sorted by health score (best first)
   * 
   * @example
   * ```typescript
   * const ranked = cp.rankProviders(['openai', 'groq', 'gemini', 'huggingface']);
   * console.log(ranked); // ['groq', 'openai', 'gemini', 'huggingface']
   * 
   * // Try providers in order until one succeeds
   * for (const provider of ranked) {
   *   try {
   *     return await callProvider(provider);
   *   } catch (error) {
   *     console.log(`${provider} failed, trying next...`);
   *   }
   * }
   * ```
   */
  rankProviders(providers: string[]): string[] {
    const scored = providers.map(provider => ({
      provider,
      score: this.scorer.calculateScore(provider),
    }));

    // Sort descending by score (higher = better)
    scored.sort((a, b) => b.score - a.score);

    return scored.map(s => s.provider);
  }

  /**
   * Execute a request through the full control plane pipeline.
   * Orchestrates idempotency, rate limiting, circuit breaking, and tracing.
   * 
   * Pipeline steps:
   * 1. Generate idempotency fingerprint
   * 2. Check idempotency cache (early return if hit)
   * 3. Check rate limit (throw if exceeded)
   * 4. Execute handler through circuit breaker
   * 5. Cache response
   * 6. Return result + trace
   * 
   * @param requestId - Unique request identifier
   * @param tenantKey - Tenant/API key for rate limiting
   * @param payload - Request payload for idempotency fingerprinting
   * @param handler - Async function to execute
   * @returns Execution result with trace
   * 
   * @throws {RateLimitError} If rate limit is exceeded
   * @throws {CircuitOpenError} If circuit breaker is open
   */
  async execute<T>(
    requestId: string,
    tenantKey: string,
    payload: Record<string, any>,
    handler: () => Promise<T>
  ): Promise<ExecutionResult<T>> {
    const trace = new ExecutionTraceBuilder(requestId);

    try {
      // Step 1: Idempotency check
      trace.start(TraceStep.IDEMPOTENCY);
      const idempotencyKey = await this.idempotency.fingerprint(payload);
      const cached = await this.idempotency.get(idempotencyKey);
      
      if (cached) {
        trace.end(TraceStep.IDEMPOTENCY, { cacheHit: true, key: idempotencyKey });
        return { result: cached.body as T, trace };
      }
      trace.end(TraceStep.IDEMPOTENCY, { cacheHit: false, key: idempotencyKey });

      // Step 2: Rate limit check
      trace.start(TraceStep.RATE_LIMIT);
      const rateLimitResult = await this.rateLimit.check(tenantKey);
      
      if (!rateLimitResult.allowed) {
        const error = new Error(`Rate limit exceeded. Retry after ${rateLimitResult.retryAfter}s`);
        error.name = 'RateLimitError';
        trace.error(TraceStep.RATE_LIMIT, error, {
          allowed: false,
          retryAfter: rateLimitResult.retryAfter,
        });
        throw error;
      }
      trace.end(TraceStep.RATE_LIMIT, {
        allowed: true,
        remaining: rateLimitResult.remaining,
      });

      // Step 3: Execute handler
      trace.start(TraceStep.EXECUTION);
      const result = await handler();
      trace.end(TraceStep.EXECUTION);

      // Step 4: Cache response
      trace.start(TraceStep.FINALIZE);
      await this.idempotency.set(idempotencyKey, result, 200);
      trace.end(TraceStep.FINALIZE, { cached: true });

      return { result, trace };
    } catch (error) {
      // Error already traced in the appropriate step
      throw error;
    }
  }
}

/**
 * Configuration for InfrGateClient.
 */
export interface InfrGateClientConfig {
  /** Base URL of the InfrGate gateway */
  baseUrl: string;
  /** API key for authentication */
  apiKey: string;
  /** Control plane configuration (optional) */
  controlPlane?: ControlPlaneConfig;
  /** Injectable fetch implementation for testing (optional) */
  fetch?: typeof fetch;
}

/**
 * OpenAI-compatible chat completion parameters.
 */
export interface ChatCompletionParams {
  /** Model identifier (e.g., 'gpt-4', 'claude-3-opus') */
  model: string;
  /** Array of conversation messages */
  messages: Array<{
    role: 'system' | 'user' | 'assistant';
    content: string;
  }>;
  /** Sampling temperature (0.0 to 2.0) */
  temperature?: number;
  /** Maximum tokens to generate */
  maxTokens?: number;
  /** Enable streaming (not yet supported in v0.2.0) */
  stream?: boolean;
  /** Custom idempotency key (auto-generated if not provided) */
  idempotencyKey?: string;
}

/**
 * OpenAI-compatible chat completion response.
 */
export interface ChatCompletionResponse {
  /** Unique response identifier */
  id: string;
  /** Model that generated the response */
  model: string;
  /** Generated completion choices */
  choices: Array<{
    message: {
      role: string;
      content: string;
    };
    finishReason: string;
  }>;
  /** Token usage statistics */
  usage: {
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
  };
}

/**
 * InfrGateClient provides an OpenAI-compatible API client with built-in control plane.
 * Drop-in replacement for OpenAI SDK with automatic idempotency, rate limiting, and circuit breaking.
 * 
 * @example
 * ```typescript
 * const client = new InfrGateClient({
 *   baseUrl: 'https://gateway.infrgate.dev',
 *   apiKey: 'sk_live_...',
 *   controlPlane: {
 *     rateLimit: { capacity: 60, refillRate: 1 }, // 60 RPM
 *   },
 * });
 * 
 * const response = await client.createChatCompletion({
 *   model: 'gpt-4',
 *   messages: [{ role: 'user', content: 'Hello!' }],
 * });
 * 
 * console.log(response.choices[0].message.content);
 * ```
 */
export class InfrGateClient {
  private config: InfrGateClientConfig;
  private controlPlane: InfrGateControlPlane;

  /**
   * Create a new InfrGate client.
   * 
   * @param config - Client configuration
   */
  constructor(config: InfrGateClientConfig) {
    this.config = config;
    this.controlPlane = new InfrGateControlPlane(config.controlPlane);
  }

  /**
   * Create a chat completion using OpenAI-compatible API.
   * 
   * @param params - Chat completion parameters
   * @returns Chat completion response
   * @throws {Error} If request fails or control plane rejects
   */
  async createChatCompletion(params: ChatCompletionParams): Promise<ChatCompletionResponse> {
    const requestId = `req_${crypto.randomUUID()}`;
    const tenantKey = this.config.apiKey;

    const payload = {
      model: params.model,
      messages: params.messages,
      temperature: params.temperature,
      max_tokens: params.maxTokens,
    };

    const { result } = await this.controlPlane.execute(
      requestId,
      tenantKey,
      payload,
      async () => {
        const url = `${this.config.baseUrl}/v1/chat/completions`;
        const headers: Record<string, string> = {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.config.apiKey}`,
        };

        if (params.idempotencyKey) {
          headers['Idempotency-Key'] = params.idempotencyKey;
        }

        const fetchFn = this.config.fetch ?? fetch;
        const response = await fetchFn(url, {
          method: 'POST',
          headers,
          body: JSON.stringify(payload),
        });

        if (!response.ok) {
          throw new Error(`Provider error: ${response.status} ${response.statusText}`);
        }

        return response.json();
      }
    );

    return result as ChatCompletionResponse;
  }
}
