import { canonicalize } from './utils/canonicalize.js';
import { sha256 } from './utils/crypto.js';

/**
 * Configuration options for IdempotencyGuard.
 */
export interface IdempotencyOptions {
  /** Time-to-live for cached responses in seconds. Default: 3600 (1 hour) */
  ttlSeconds: number;
  /** Hash algorithm to use. Currently only SHA-256 is supported. */
  hashAlgorithm?: 'SHA-256';
}

/**
 * Cached response stored in the idempotency cache.
 */
export interface CachedResponse {
  /** HTTP status code of the cached response */
  statusCode: number;
  /** Response body (arbitrary JSON-serializable data) */
  body: any;
  /** Timestamp when the response was cached */
  cachedAt: Date;
}

/**
 * Internal cache entry with expiration timestamp.
 */
interface CacheEntry {
  response: CachedResponse;
  expiresAt: number; // Unix timestamp in milliseconds
}

/**
 * IdempotencyGuard provides exactly-once execution semantics for API requests.
 * Uses SHA-256 fingerprinting with TTL-based replay cache to prevent duplicate processing.
 * 
 * @example
 * ```typescript
 * const guard = new IdempotencyGuard({ ttlSeconds: 3600 });
 * 
 * const payload = { model: 'gpt-4', messages: [{ role: 'user', content: 'Hello' }] };
 * const key = await guard.fingerprint(payload);
 * 
 * const cached = await guard.get(key);
 * if (cached) {
 *   return cached.body; // Replay cached response
 * }
 * 
 * const response = await callLLMProvider(payload);
 * await guard.set(key, response, 200);
 * ```
 */
export class IdempotencyGuard {
  private cache: Map<string, CacheEntry> = new Map();
  private options: IdempotencyOptions;

  /**
   * Create a new IdempotencyGuard.
   * 
   * @param options - Configuration options (optional)
   */
  constructor(options?: Partial<IdempotencyOptions>) {
    this.options = {
      ttlSeconds: 3600,
      hashAlgorithm: 'SHA-256',
      ...options,
    };
  }

  /**
   * Generate a deterministic SHA-256 fingerprint of a request payload.
   * Uses canonical JSON serialization (sorted keys) to ensure consistent hashing.
   * 
   * @param payload - Arbitrary JSON-serializable object
   * @returns Hex-encoded SHA-256 hash (64 characters, lowercase)
   * 
   * @example
   * ```typescript
   * const key1 = await guard.fingerprint({ b: 2, a: 1 });
   * const key2 = await guard.fingerprint({ a: 1, b: 2 });
   * console.log(key1 === key2); // true (order-independent)
   * ```
   */
  async fingerprint(payload: Record<string, any>): Promise<string> {
    const canonical = canonicalize(payload);
    return sha256(canonical);
  }

  /**
   * Retrieve a cached response by idempotency key.
   * Automatically evicts expired entries.
   * 
   * @param key - Idempotency key (typically from fingerprint())
   * @returns Cached response if found and not expired, null otherwise
   */
  async get(key: string): Promise<CachedResponse | null> {
    const entry = this.cache.get(key);
    
    if (!entry) {
      return null;
    }

    // Lazy eviction: check if entry has expired
    if (Date.now() >= entry.expiresAt) {
      this.cache.delete(key);
      return null;
    }

    return entry.response;
  }

  /**
   * Store a response in the idempotency cache with TTL.
   * 
   * @param key - Idempotency key
   * @param response - Response body to cache
   * @param statusCode - HTTP status code
   */
  async set(key: string, response: any, statusCode: number): Promise<void> {
    const expiresAt = Date.now() + (this.options.ttlSeconds * 1000);
    
    this.cache.set(key, {
      response: {
        statusCode,
        body: response,
        cachedAt: new Date(),
      },
      expiresAt,
    });
  }

  /**
   * Clear the entire cache. Useful for testing.
   */
  clear(): void {
    this.cache.clear();
  }
}
