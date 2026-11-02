/**
 * Configuration for token bucket rate limiter.
 */
export interface RateLimitConfig {
  /** Maximum tokens in the bucket */
  capacity: number;
  /** Tokens refilled per second */
  refillRate: number;
  /** Key prefix for namespacing (default: 'ratelimit') */
  keyPrefix?: string;
}

/**
 * Result of a rate limit check.
 */
export interface RateLimitResult {
  /** Whether the request is allowed under the rate limit */
  allowed: boolean;
  /** Number of tokens remaining after this request */
  remaining: number;
  /** Timestamp when the bucket will be fully refilled */
  resetAt: Date;
  /** Seconds to wait before retrying (only present if denied) */
  retryAfter?: number;
}

/**
 * Internal state of a token bucket.
 */
interface BucketState {
  /** Current number of tokens in the bucket */
  tokens: number;
  /** Timestamp of last refill (milliseconds) */
  lastRefill: number;
}

/**
 * TokenBucketRateLimiter implements the token bucket algorithm for rate limiting.
 * Provides per-key rate limiting with linear token refill.
 * 
 * @example
 * ```typescript
 * const limiter = new TokenBucketRateLimiter({
 *   capacity: 60,    // 60 requests
 *   refillRate: 1,   // per second (60 RPM)
 * });
 * 
 * const result = await limiter.check('user-123');
 * if (!result.allowed) {
 *   return {
 *     error: 'Rate limit exceeded',
 *     retryAfter: result.retryAfter,
 *   };
 * }
 * ```
 */
export class TokenBucketRateLimiter {
  private config: RateLimitConfig;
  private buckets: Map<string, BucketState> = new Map();

  /**
   * Create a new TokenBucketRateLimiter.
   * 
   * @param config - Rate limit configuration
   */
  constructor(config: RateLimitConfig) {
    this.config = {
      keyPrefix: 'ratelimit',
      ...config,
    };
  }

  /**
   * Check if a request is allowed under the rate limit.
   * Consumes tokens from the bucket if allowed.
   * 
   * @param key - Rate limit key (e.g., tenant ID, API key)
   * @param tokens - Number of tokens to consume (default: 1)
   * @returns Rate limit result with allowed status and metadata
   * 
   * @example
   * ```typescript
   * const result = await limiter.check('tenant-abc', 5);
   * console.log(result.allowed);    // true/false
   * console.log(result.remaining);  // tokens left
   * console.log(result.retryAfter); // seconds to wait (if denied)
   * ```
   */
  async check(key: string, tokens: number = 1): Promise<RateLimitResult> {
    const bucketKey = `${this.config.keyPrefix}:${key}`;
    const now = Date.now();

    // Get or initialize bucket state
    let state = this.buckets.get(bucketKey);
    if (!state) {
      state = {
        tokens: this.config.capacity,
        lastRefill: now,
      };
    }

    // Refill tokens based on elapsed time
    state = this.refill(state, now);

    // Check if we have enough tokens
    if (state.tokens >= tokens) {
      // Allow request and consume tokens
      state.tokens -= tokens;
      this.buckets.set(bucketKey, state);

      return {
        allowed: true,
        remaining: Math.floor(state.tokens),
        resetAt: new Date(now + (this.config.capacity / this.config.refillRate) * 1000),
      };
    }

    // Rate limit exceeded
    const deficit = tokens - state.tokens;
    const retryAfter = Math.ceil(deficit / this.config.refillRate);

    return {
      allowed: false,
      remaining: 0,
      resetAt: new Date(now + retryAfter * 1000),
      retryAfter,
    };
  }

  /**
   * Refill tokens in a bucket based on elapsed time.
   * 
   * @param state - Current bucket state
   * @param now - Current timestamp (milliseconds)
   * @returns Updated bucket state
   */
  private refill(state: BucketState, now: number): BucketState {
    const elapsedSeconds = (now - state.lastRefill) / 1000;
    const tokensToAdd = elapsedSeconds * this.config.refillRate;
    const newTokens = Math.min(
      state.tokens + tokensToAdd,
      this.config.capacity
    );

    return {
      tokens: newTokens,
      lastRefill: now,
    };
  }

  /**
   * Reset the bucket for a specific key. Useful for testing.
   * 
   * @param key - Rate limit key to reset
   */
  reset(key: string): void {
    const bucketKey = `${this.config.keyPrefix}:${key}`;
    this.buckets.delete(bucketKey);
  }
}
