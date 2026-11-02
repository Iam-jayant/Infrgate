/**
 * Gateway execution trace steps.
 * Represents the 7-step control plane pipeline.
 */
export enum TraceStep {
  /** Authentication: API key validation and tenant resolution */
  AUTH = 'auth',
  /** Rate limiting: Token bucket checks and concurrency limits */
  RATE_LIMIT = 'rate_limit',
  /** Idempotency: SHA-256 fingerprint and cache replay */
  IDEMPOTENCY = 'idempotency',
  /** Policy enforcement: Spend limits and guardrails (FAST-TRACK ADJUSTMENT) */
  POLICY = 'policy',
  /** Provider routing: Adapter selection and circuit breaker checks */
  ROUTING = 'routing',
  /** Execution: LLM provider API call and response streaming */
  EXECUTION = 'execution',
  /** Finalization: Usage persistence and response caching */
  FINALIZE = 'finalize',
}

/**
 * Status of a trace step for UI rendering (FAST-TRACK ADJUSTMENT).
 */
export type TraceStepStatus = 'passed' | 'failed' | 'waiting' | 'skipped';

/**
 * Event recorded during gateway execution.
 */
export interface TraceEvent {
  /** The step this event belongs to */
  step: TraceStep;
  /** Timestamp when the event occurred */
  timestamp: Date;
  /** Duration in milliseconds (only present after step completes) */
  durationMs?: number;
  /** Additional metadata about the step */
  metadata?: Record<string, any>;
  /** Error information if the step failed */
  error?: {
    type: string;
    message: string;
  };
}

/**
 * Summary of a trace step for UI rendering (FAST-TRACK ADJUSTMENT).
 */
export interface TraceStepSummary {
  /** The step name */
  step: TraceStep;
  /** Current status of the step */
  status: TraceStepStatus;
  /** Duration in milliseconds (undefined if not completed) */
  durationMs?: number;
  /** Additional metadata */
  metadata?: Record<string, any>;
}

/**
 * ExecutionTraceBuilder captures detailed telemetry for gateway request processing.
 * Tracks timing, metadata, and errors for each step in the control plane pipeline.
 * 
 * @example
 * ```typescript
 * const trace = new ExecutionTraceBuilder('req_abc123');
 * 
 * trace.start(TraceStep.AUTH);
 * // ... perform authentication ...
 * trace.end(TraceStep.AUTH, { tenantId: 'tenant_xyz' });
 * 
 * trace.start(TraceStep.RATE_LIMIT);
 * try {
 *   await checkRateLimit();
 *   trace.end(TraceStep.RATE_LIMIT, { allowed: true });
 * } catch (error) {
 *   trace.error(TraceStep.RATE_LIMIT, error, { allowed: false });
 *   throw error;
 * }
 * 
 * console.log(trace.getTotalDurationMs()); // Total request time
 * console.log(JSON.stringify(trace.toJSON(), null, 2));
 * ```
 */
export class ExecutionTraceBuilder {
  private events: TraceEvent[] = [];
  private stepStartTimes: Map<TraceStep, number> = new Map();

  /**
   * Create a new execution trace builder.
   * 
   * @param requestId - Unique identifier for this request
   */
  constructor(private readonly requestId: string) {}

  /**
   * Start tracking a trace step.
   * Records start time using high-precision performance timer.
   * 
   * @param step - The step to start
   * @param metadata - Optional metadata to attach
   */
  start(step: TraceStep, metadata?: Record<string, any>): void {
    const startTime = performance.now();
    this.stepStartTimes.set(step, startTime);

    this.events.push({
      step,
      timestamp: new Date(),
      metadata,
    });
  }

  /**
   * End tracking a trace step.
   * Calculates duration from start time and updates the event.
   * 
   * @param step - The step to end
   * @param metadata - Optional metadata to merge with start metadata
   * @throws {Error} If step was never started
   */
  end(step: TraceStep, metadata?: Record<string, any>): void {
    const startTime = this.stepStartTimes.get(step);
    if (startTime === undefined) {
      throw new Error(`Step ${step} was never started`);
    }

    const durationMs = performance.now() - startTime;
    this.stepStartTimes.delete(step);

    // Find the corresponding start event and update it
    const startEvent = this.events.find(
      e => e.step === step && e.durationMs === undefined
    );

    if (startEvent) {
      startEvent.durationMs = durationMs;
      if (metadata) {
        startEvent.metadata = { ...startEvent.metadata, ...metadata };
      }
    }
  }

  /**
   * Record an error for a trace step.
   * Marks the step as failed and captures error details.
   * 
   * @param step - The step that failed
   * @param error - The error that occurred
   * @param metadata - Optional metadata to attach
   */
  error(step: TraceStep, error: Error, metadata?: Record<string, any>): void {
    const startTime = this.stepStartTimes.get(step);
    const durationMs = startTime !== undefined ? performance.now() - startTime : undefined;
    
    if (startTime !== undefined) {
      this.stepStartTimes.delete(step);
    }

    // Find the corresponding start event and add error info
    const startEvent = this.events.find(
      e => e.step === step && e.error === undefined
    );

    if (startEvent) {
      startEvent.durationMs = durationMs;
      startEvent.error = {
        type: error.name || 'Error',
        message: error.message,
      };
      if (metadata) {
        startEvent.metadata = { ...startEvent.metadata, ...metadata };
      }
    } else {
      // If no start event found, create a new error event
      this.events.push({
        step,
        timestamp: new Date(),
        durationMs,
        error: {
          type: error.name || 'Error',
          message: error.message,
        },
        metadata,
      });
    }
  }

  /**
   * Get all trace events.
   * 
   * @returns Array of trace events in chronological order
   */
  getEvents(): TraceEvent[] {
    return [...this.events];
  }

  /**
   * Get trace summary for UI rendering (FAST-TRACK ADJUSTMENT).
   * Returns simplified view with status for each step.
   * 
   * @returns Array of step summaries
   */
  toSummary(): TraceStepSummary[] {
    const summaries: TraceStepSummary[] = [];

    for (const event of this.events) {
      let status: TraceStepStatus;
      
      if (event.error) {
        status = 'failed';
      } else if (event.durationMs !== undefined) {
        status = 'passed';
      } else if (this.stepStartTimes.has(event.step)) {
        status = 'waiting';
      } else {
        status = 'skipped';
      }

      summaries.push({
        step: event.step,
        status,
        durationMs: event.durationMs,
        metadata: event.metadata,
      });
    }

    return summaries;
  }

  /**
   * Export trace as JSON string.
   * Includes request ID, all events, and total duration.
   * 
   * @returns JSON representation of the trace
   */
  toJSON(): string {
    return JSON.stringify({
      requestId: this.requestId,
      events: this.events,
      totalDurationMs: this.getTotalDurationMs(),
    }, null, 2);
  }

  /**
   * Get total duration of the trace.
   * Calculated from first event start to last event end.
   * 
   * @returns Total duration in milliseconds, or 0 if no completed events
   */
  getTotalDurationMs(): number {
    if (this.events.length === 0) {
      return 0;
    }

    const completedEvents = this.events.filter(e => e.durationMs !== undefined);
    if (completedEvents.length === 0) {
      return 0;
    }

    const firstTimestamp = this.events[0].timestamp.getTime();
    const lastEvent = completedEvents[completedEvents.length - 1];
    const lastTimestamp = lastEvent.timestamp.getTime() + (lastEvent.durationMs || 0);

    return lastTimestamp - firstTimestamp;
  }
}
