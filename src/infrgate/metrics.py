"""
Prometheus metrics definition for InfrGate.
"""

from prometheus_client import Counter, Histogram, Gauge

# -- Counters --

REQUESTS_TOTAL = Counter(
    "infrgate_requests_total",
    "Total inference requests",
    ["tenant", "model", "status"],
)

TOKENS_TOTAL = Counter(
    "infrgate_tokens_total",
    "Total tokens consumed",
    ["tenant", "type"],  # type can be 'prompt' or 'completion'
)

PROVIDER_REQUESTS_TOTAL = Counter(
    "infrgate_provider_requests_total",
    "Provider calls",
    ["provider", "status"],
)

PROVIDER_RETRIES_TOTAL = Counter(
    "infrgate_provider_retries_total",
    "Retry attempts",
    ["provider"],
)

PROVIDER_TIMEOUTS_TOTAL = Counter(
    "infrgate_provider_timeouts_total",
    "Timeout events",
    ["provider"],
)

CIRCUIT_STATE_CHANGES_TOTAL = Counter(
    "infrgate_circuit_state_changes_total",
    "Circuit state transitions",
    ["provider", "transition"],
)

FAILOVERS_TOTAL = Counter(
    "infrgate_failovers_total",
    "Failover events",
    ["from_provider", "to_provider"],
)

RATE_LIMIT_REJECTIONS_TOTAL = Counter(
    "infrgate_rate_limit_rejections_total",
    "Rate limit rejections",
    ["tenant"],
)

AUTH_FAILURES_TOTAL = Counter(
    "infrgate_auth_failures_total",
    "Authentication failures",
    ["reason"],
)

JOBS_PROCESSED_TOTAL = Counter(
    "infrgate_jobs_processed_total",
    "Worker jobs processed",
    ["job_type", "status"],
)

# Webhooks
WEBHOOKS_ENQUEUED_TOTAL = Counter(
    "infrgate_webhooks_enqueued_total",
    "Total number of webhooks enqueued for delivery",
    ["event_type"]
)

WEBHOOKS_DELIVERED_TOTAL = Counter(
    "infrgate_webhooks_delivered_total",
    "Total number of webhooks delivered",
    ["event_type", "status"]
)

IDEMPOTENCY_CAS_CONFLICTS = Counter(
    "infrgate_idempotency_cas_conflicts_total",
    "Total number of compare-and-swap conflicts during idempotency finalization"
)

# -- Histograms --

REQUEST_DURATION = Histogram(
    "infrgate_request_duration_seconds",
    "End-to-end request duration",
    ["model"],
    buckets=[0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30],
)

PROVIDER_LATENCY = Histogram(
    "infrgate_provider_latency_seconds",
    "Upstream provider latency",
    ["provider"],
    buckets=[0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30],
)

JOB_DURATION = Histogram(
    "infrgate_job_duration_seconds",
    "Background job duration",
    ["job_type"],
    buckets=[0.1, 0.5, 1, 5, 10, 30, 60],
)

# -- Gauges --

PROVIDER_CIRCUIT_STATE = Gauge(
    "infrgate_provider_circuit_state",
    "0=closed, 1=open, 2=half_open",
    ["provider"],
)

ACTIVE_REQUESTS = Gauge(
    "infrgate_active_requests",
    "Currently in-flight requests",
)

JOBS_PENDING = Gauge(
    "infrgate_jobs_pending",
    "Pending jobs in queue",
    ["job_type"],
)
