"""
Custom exception hierarchy for InfrGate.

All exceptions map to specific HTTP status codes and error types
used in the standard error envelope.

Spec reference: 02-api-design.md §4.2, 06-provider-adapters.md §2.2
"""

from __future__ import annotations


class InfrGateError(Exception):
    """Base exception for all InfrGate errors."""

    status_code: int = 500
    error_type: str = "internal_error"

    def __init__(self, message: str = "An unexpected error occurred."):
        self.message = message
        super().__init__(message)


# ── Policy errors ─────────────────────────────────────────────────────────


class SpendCapExceeded(InfrGateError):
    """Tenant has exceeded their monthly spend cap."""

    status_code = 403
    error_type = "spend_cap_exceeded"

    def __init__(self):
        super().__init__("Monthly spend cap exceeded.")


class ModelNotAllowed(InfrGateError):
    """Requested model is not in the tenant's plan."""

    status_code = 403
    error_type = "model_not_allowed"

    def __init__(self, model: str, plan: str):
        super().__init__(
            f"Model '{model}' is not available on the '{plan}' plan."
        )


class TenantSuspended(InfrGateError):
    """Tenant account is suspended."""

    status_code = 403
    error_type = "tenant_suspended"

    def __init__(self):
        super().__init__("Tenant account is suspended.")


class RateLimitExceeded(InfrGateError):
    """Per-tenant rate limit exceeded."""

    status_code = 429
    error_type = "rate_limit_exceeded"

    def __init__(self, retry_after: int = 1):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after} seconds.")


# ── Provider errors ───────────────────────────────────────────────────────


class ProviderError(InfrGateError):
    """Base exception for all provider-related errors."""

    status_code = 502
    error_type = "provider_error"

    def __init__(
        self,
        message: str,
        provider: str,
        upstream_status: int | None = None,
        retryable: bool = False,
        raw_response: dict | None = None,
    ):
        self.provider = provider
        self.upstream_status = upstream_status
        self.retryable = retryable
        self.raw_response = raw_response
        super().__init__(message)


class ProviderTimeoutError(ProviderError):
    """Upstream provider request timed out."""

    status_code = 504
    error_type = "provider_timeout"

    def __init__(self, provider: str, timeout_seconds: float):
        super().__init__(
            message=f"Request to {provider} timed out after {timeout_seconds}s.",
            provider=provider,
            retryable=True,
        )


class ProviderRateLimitError(ProviderError):
    """Upstream provider rate-limited our request."""

    error_type = "provider_error"

    def __init__(self, provider: str, retry_after: int | None = None):
        self.provider_retry_after = retry_after
        super().__init__(
            message=f"Rate limited by {provider}.",
            provider=provider,
            upstream_status=429,
            retryable=True,
        )


class ProviderAuthError(ProviderError):
    """Upstream provider rejected our credentials."""

    error_type = "provider_error"

    def __init__(self, provider: str):
        super().__init__(
            message=f"Authentication failed with {provider}. Check API key configuration.",
            provider=provider,
            upstream_status=401,
            retryable=False,
        )
