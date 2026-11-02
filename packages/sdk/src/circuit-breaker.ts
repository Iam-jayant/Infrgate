/**
 * Circuit breaker states following the state machine pattern.
 */
export enum CircuitState {
  /** Normal operation - all requests allowed */
  CLOSED = 'CLOSED',
  /** Failure threshold exceeded - requests rejected immediately */
  OPEN = 'OPEN',
  /** Recovery probe - limited requests allowed to test health */
  HALF_OPEN = 'HALF_OPEN',
}

/**
 * Configuration for circuit breaker behavior.
 */
export interface CircuitBreakerConfig {
  /** Number of failures before opening circuit (default: 5) */
  failureThreshold: number;
  /** Time window for counting failures in milliseconds (default: 60000) */
  failureWindowMs: number;
  /** Time to wait before attempting HALF_OPEN in milliseconds (default: 30000) */
  recoveryTimeoutMs: number;
  /** Number of successes needed to close from HALF_OPEN (default: 2) */
  successThreshold: number;
  /** Maximum concurrent requests in HALF_OPEN state (default: 3) */
  halfOpenMaxRequests: number;
}

/**
 * Current status of a circuit breaker.
 */
export interface CircuitStatus {
  /** Current state of the circuit */
  state: CircuitState;
  /** Number of failures in the current window */
  failureCount: number;
  /** Timestamp of the last failure */
  lastFailureAt?: Date;
  /** Timestamp when the circuit was opened */
  openedAt?: Date;
  /** Number of consecutive successes in HALF_OPEN state */
  halfOpenSuccessCount?: number;
}

/**
 * Failure record in the sliding window.
 */
interface FailureWindow {
  timestamp: number;
  error: string;
}

/**
 * Error thrown when circuit breaker is in OPEN state.
 */
export class CircuitOpenError extends Error {
  constructor(
    public readonly providerName: string,
    public readonly willRetryAt: Date
  ) {
    super(
      `Circuit breaker OPEN for provider "${providerName}". ` +
      `Will attempt recovery at ${willRetryAt.toISOString()}`
    );
    this.name = 'CircuitOpenError';
  }
}

/**
 * ProviderCircuitBreaker implements the circuit breaker pattern for fault tolerance.
 * Prevents cascading failures by failing fast when a provider is unhealthy.
 * 
 * State transitions:
 * - CLOSED → OPEN: When failure count >= threshold within window
 * - OPEN → HALF_OPEN: After recovery timeout elapses
 * - HALF_OPEN → CLOSED: After success count >= threshold
 * - HALF_OPEN → OPEN: On any failure
 * 
 * @example
 * ```typescript
 * const breaker = new ProviderCircuitBreaker('openai', {
 *   failureThreshold: 5,
 *   recoveryTimeoutMs: 30000,
 * });
 * 
 * try {
 *   const result = await breaker.execute(async () => {
 *     return await fetch('https://api.openai.com/...');
 *   });
 * } catch (error) {
 *   if (error instanceof CircuitOpenError) {
 *     console.log('Circuit is open, trying fallback provider');
 *   }
 * }
 * ```
 */
export class ProviderCircuitBreaker {
  private state: CircuitState = CircuitState.CLOSED;
  private failures: FailureWindow[] = [];
  private halfOpenCount: number = 0;
  private openedAt?: number;
  private halfOpenSuccessCount: number = 0;
  private config: CircuitBreakerConfig;

  /**
   * Create a new circuit breaker for a provider.
   * 
   * @param providerName - Name of the provider (for error messages)
   * @param config - Circuit breaker configuration (optional)
   */
  constructor(
    private readonly providerName: string,
    config?: Partial<CircuitBreakerConfig>
  ) {
    this.config = {
      failureThreshold: 5,
      failureWindowMs: 60000,
      recoveryTimeoutMs: 30000,
      successThreshold: 2,
      halfOpenMaxRequests: 3,
      ...config,
    };
  }

  /**
   * Get the current state of the circuit breaker.
   */
  getState(): CircuitState {
    // Check if we should transition from OPEN to HALF_OPEN
    if (this.state === CircuitState.OPEN && this.canAttemptHalfOpen()) {
      this.transition(CircuitState.HALF_OPEN);
    }
    return this.state;
  }

  /**
   * Execute a function with circuit breaker protection.
   * Automatically records success or failure and manages state transitions.
   * 
   * @param fn - Async function to execute
   * @returns Result of the function
   * @throws {CircuitOpenError} If circuit is OPEN
   * @throws Original error if function fails
   */
  async execute<T>(fn: () => Promise<T>): Promise<T> {
    const currentState = this.getState();

    // Reject immediately if circuit is OPEN
    if (currentState === CircuitState.OPEN) {
      const willRetryAt = new Date(this.openedAt! + this.config.recoveryTimeoutMs);
      throw new CircuitOpenError(this.providerName, willRetryAt);
    }

    // Limit concurrent requests in HALF_OPEN
    if (currentState === CircuitState.HALF_OPEN) {
      if (this.halfOpenCount >= this.config.halfOpenMaxRequests) {
        const willRetryAt = new Date(this.openedAt! + this.config.recoveryTimeoutMs);
        throw new CircuitOpenError(this.providerName, willRetryAt);
      }
      this.halfOpenCount++;
    }

    try {
      const result = await fn();
      this.recordSuccess();
      return result;
    } catch (error) {
      this.recordFailure(error as Error);
      throw error;
    } finally {
      if (currentState === CircuitState.HALF_OPEN) {
        this.halfOpenCount--;
      }
    }
  }

  /**
   * Record a successful execution.
   * Updates HALF_OPEN success counter and may transition to CLOSED.
   */
  recordSuccess(): void {
    if (this.state === CircuitState.HALF_OPEN) {
      this.halfOpenSuccessCount++;
      if (this.halfOpenSuccessCount >= this.config.successThreshold) {
        this.transition(CircuitState.CLOSED);
      }
    }
  }

  /**
   * Record a failed execution.
   * Updates failure window and may transition to OPEN.
   * 
   * @param error - The error that occurred
   */
  recordFailure(error: Error): void {
    const now = Date.now();
    
    this.failures.push({
      timestamp: now,
      error: error.message,
    });

    // Transition from HALF_OPEN to OPEN immediately on any failure
    if (this.state === CircuitState.HALF_OPEN) {
      this.transition(CircuitState.OPEN);
      return;
    }

    // Check if we should open the circuit from CLOSED
    if (this.state === CircuitState.CLOSED && this.shouldOpen()) {
      this.transition(CircuitState.OPEN);
    }
  }

  /**
   * Get detailed status of the circuit breaker.
   */
  getStatus(): CircuitStatus {
    this.getState(); // Ensure state is up-to-date
    
    const recentFailures = this.getRecentFailures();
    
    return {
      state: this.state,
      failureCount: recentFailures.length,
      lastFailureAt: recentFailures.length > 0 
        ? new Date(recentFailures[recentFailures.length - 1].timestamp)
        : undefined,
      openedAt: this.openedAt ? new Date(this.openedAt) : undefined,
      halfOpenSuccessCount: this.state === CircuitState.HALF_OPEN 
        ? this.halfOpenSuccessCount 
        : undefined,
    };
  }

  /**
   * Reset the circuit breaker to CLOSED state. Useful for testing.
   */
  reset(): void {
    this.state = CircuitState.CLOSED;
    this.failures = [];
    this.halfOpenCount = 0;
    this.openedAt = undefined;
    this.halfOpenSuccessCount = 0;
  }

  /**
   * Transition to a new state and reset state-specific counters.
   */
  private transition(newState: CircuitState): void {
    this.state = newState;

    if (newState === CircuitState.OPEN) {
      this.openedAt = Date.now();
      this.halfOpenSuccessCount = 0;
    } else if (newState === CircuitState.HALF_OPEN) {
      this.halfOpenSuccessCount = 0;
      this.halfOpenCount = 0;
    } else if (newState === CircuitState.CLOSED) {
      this.failures = [];
      this.openedAt = undefined;
      this.halfOpenSuccessCount = 0;
    }
  }

  /**
   * Check if failure threshold is exceeded in the current window.
   */
  private shouldOpen(): boolean {
    const recentFailures = this.getRecentFailures();
    return recentFailures.length >= this.config.failureThreshold;
  }

  /**
   * Get failures within the current sliding window.
   */
  private getRecentFailures(): FailureWindow[] {
    const now = Date.now();
    const windowStart = now - this.config.failureWindowMs;
    
    // Filter to failures within window
    this.failures = this.failures.filter(f => f.timestamp >= windowStart);
    return this.failures;
  }

  /**
   * Check if enough time has passed to attempt HALF_OPEN.
   */
  private canAttemptHalfOpen(): boolean {
    if (!this.openedAt) return false;
    return Date.now() >= this.openedAt + this.config.recoveryTimeoutMs;
  }
}

/**
 * Health signals tracked by the adapter scorer.
 */
export interface HealthSignals {
  /** Availability score: 1.0 (circuit CLOSED) to 0.0 (circuit OPEN) */
  availability: number;
  /** EWMA error rate: 0.0 (no errors) to 1.0 (all errors) */
  ewmaErrorRate: number;
  /** EWMA latency in milliseconds */
  ewmaLatencyMs: number;
}

/**
 * AdapterScorer tracks provider health using exponentially weighted moving averages (EWMA).
 * Enables intelligent multi-provider routing by scoring adapters based on availability,
 * error rate, and latency.
 * 
 * @example
 * ```typescript
 * const scorer = new AdapterScorer(0.3); // alpha = 0.3
 * 
 * // Record metrics after each request
 * scorer.record('openai', 245, false);   // 245ms, success
 * scorer.record('groq', 89, false);      // 89ms, success
 * scorer.record('openai', 512, true);    // 512ms, error
 * 
 * // Calculate composite scores
 * const openaiScore = scorer.calculateScore('openai');
 * const groqScore = scorer.calculateScore('groq');
 * 
 * if (groqScore > openaiScore) {
 *   console.log('Route to Groq (better health)');
 * }
 * ```
 */
export class AdapterScorer {
  private healthMetrics: Map<string, HealthSignals> = new Map();
  private alpha: number;

  /**
   * Create a new AdapterScorer.
   * 
   * @param alpha - EWMA smoothing factor (0-1). Higher = more weight on recent samples. Default: 0.3
   */
  constructor(alpha: number = 0.3) {
    this.alpha = alpha;
  }

  /**
   * Record a health signal for a provider.
   * Updates EWMA error rate and latency.
   * 
   * @param providerName - Name of the provider
   * @param latencyMs - Request latency in milliseconds
   * @param isError - Whether the request resulted in an error
   */
  record(providerName: string, latencyMs: number, isError: boolean): void {
    const current = this.healthMetrics.get(providerName) || {
      availability: 1.0,
      ewmaErrorRate: 0.0,
      ewmaLatencyMs: 1000.0, // Neutral baseline
    };

    // Update EWMA: newValue = α * sample + (1 - α) * currentValue
    const newErrorRate = this.alpha * (isError ? 1 : 0) + (1 - this.alpha) * current.ewmaErrorRate;
    const newLatency = this.alpha * latencyMs + (1 - this.alpha) * current.ewmaLatencyMs;

    this.healthMetrics.set(providerName, {
      availability: current.availability,
      ewmaErrorRate: newErrorRate,
      ewmaLatencyMs: newLatency,
    });
  }

  /**
   * Update availability score based on circuit breaker state.
   * Should be called when circuit state changes.
   * 
   * @param providerName - Name of the provider
   * @param state - Current circuit breaker state
   */
  updateAvailability(providerName: string, state: CircuitState): void {
    const current = this.getHealth(providerName);
    
    let availability: number;
    switch (state) {
      case CircuitState.CLOSED:
        availability = 1.0;
        break;
      case CircuitState.HALF_OPEN:
        availability = 0.5;
        break;
      case CircuitState.OPEN:
        availability = 0.0;
        break;
    }

    this.healthMetrics.set(providerName, {
      ...current,
      availability,
    });
  }

  /**
   * Get current health signals for a provider.
   * Returns cold-start defaults if provider has no history.
   * 
   * @param providerName - Name of the provider
   * @returns Health signals
   */
  getHealth(providerName: string): HealthSignals {
    return this.healthMetrics.get(providerName) || {
      availability: 1.0,
      ewmaErrorRate: 0.0,
      ewmaLatencyMs: 1000.0,
    };
  }

  /**
   * Calculate composite health score for a provider.
   * Higher score = better health (0.0 to 1.0).
   * 
   * @param providerName - Name of the provider
   * @param weights - Weight distribution for scoring components (default: {availability: 0.4, errorRate: 0.4, latency: 0.2})
   * @returns Composite score clamped to [0, 1]
   */
  calculateScore(
    providerName: string,
    weights: { availability?: number; errorRate?: number; latency?: number } = {}
  ): number {
    const w = {
      availability: 0.4,
      errorRate: 0.4,
      latency: 0.2,
      ...weights,
    };

    const health = this.getHealth(providerName);

    // Normalize latency to 0-1 scale (lower is better)
    // Assume 5000ms is worst case, 0ms is best
    const latencyScore = 1 - Math.min(health.ewmaLatencyMs / 5000, 1);

    const score =
      w.availability * health.availability +
      w.errorRate * (1 - health.ewmaErrorRate) +
      w.latency * latencyScore;

    return Math.max(0, Math.min(1, score)); // Clamp to [0, 1]
  }
}
