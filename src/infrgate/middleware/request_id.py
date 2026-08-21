"""
Request ID middleware — assigns and propagates X-Request-ID.

Every request gets a unique request_id (UUID v4). If the client provides
X-Request-ID, the gateway validates and uses it; otherwise it generates one.
The request_id is attached to request state, structlog context, and the
response header.

Spec reference: 01-system-overview.md §8.1
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware that assigns and propagates X-Request-ID."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_request_id = request.headers.get("x-request-id")
        request_id = self._validate_or_generate(client_request_id)

        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)

        response.headers["X-Request-ID"] = request_id

        return response

    @staticmethod
    def _validate_or_generate(client_value: str | None) -> str:
        """Validate a client-supplied UUID or generate a new one."""
        if client_value:
            try:
                parsed = uuid.UUID(client_value)
                return str(parsed)
            except ValueError:
                pass  # Invalid UUID — generate a new one
        return str(uuid.uuid4())
