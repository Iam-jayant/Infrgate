"""
SDK-specific exceptions.

Every error from the InfrGate gateway is parsed into a typed exception
so callers can handle failures precisely without inspecting raw HTTP.
"""

from __future__ import annotations


class InfrGateError(Exception):
    """Base exception for all SDK errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        request_id: str | None = None,
        body: dict | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.request_id = request_id
        self.body = body
        super().__init__(message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"message={self.message!r}, "
            f"status_code={self.status_code}, "
            f"error_type={self.error_type!r}"
            f")"
        )


class AuthenticationError(InfrGateError):
    """401 — invalid or missing API key."""


class PermissionDeniedError(InfrGateError):
    """403 — model not allowed, spend cap exceeded, or tenant suspended."""


class NotFoundError(InfrGateError):
    """404 — resource not found."""


class ConflictError(InfrGateError):
    """409 — idempotency conflict (duplicate in-flight request)."""


class RateLimitError(InfrGateError):
    """429 — rate limit exceeded."""

    def __init__(self, message: str, *, retry_after: int | None = None, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ProviderError(InfrGateError):
    """502 — upstream provider failure."""


class ProviderUnavailableError(InfrGateError):
    """503 — all providers unavailable."""


class ProviderTimeoutError(InfrGateError):
    """504 — upstream provider timed out."""


class ConnectionError(InfrGateError):
    """Network-level connection failure (could not reach the gateway)."""


class APITimeoutError(InfrGateError):
    """The SDK's own request timed out before the gateway responded."""


# ── Mapping from HTTP status → exception class ───────────────────────────

_STATUS_MAP: dict[int, type[InfrGateError]] = {
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitError,
    502: ProviderError,
    503: ProviderUnavailableError,
    504: ProviderTimeoutError,
}


def raise_for_status(status_code: int, body: dict | None, headers: dict | None = None) -> None:
    """Parse a gateway error response and raise the appropriate exception."""
    error_data = (body or {}).get("error", {})
    message = error_data.get("message", f"HTTP {status_code}")
    error_type = error_data.get("type", "api_error")
    request_id = (headers or {}).get("x-request-id")

    exc_cls = _STATUS_MAP.get(status_code, InfrGateError)

    kwargs: dict = {
        "status_code": status_code,
        "error_type": error_type,
        "request_id": request_id,
        "body": body,
    }

    if exc_cls is RateLimitError:
        retry_after_raw = (headers or {}).get("retry-after")
        kwargs["retry_after"] = int(retry_after_raw) if retry_after_raw else None

    raise exc_cls(message, **kwargs)
