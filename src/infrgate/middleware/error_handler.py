"""
Error handler — consistent error response envelope for all exceptions.

Registers exception handlers on the FastAPI app to ensure every error
response follows the standard error envelope format.

Spec reference: 02-api-design.md §4.1
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from infrgate.exceptions import InfrGateError, RateLimitExceeded

logger = structlog.get_logger()


def register_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI application."""

    @app.exception_handler(InfrGateError)
    async def infrgate_error_handler(request: Request, exc: InfrGateError) -> JSONResponse:
        """Handle InfrGate-specific exceptions."""
        request_id = getattr(request.state, "request_id", None)

        logger.warning(
            "request_error",
            error_type=exc.error_type,
            message=exc.message,
            status_code=exc.status_code,
        )

        headers = {}
        if isinstance(exc, RateLimitExceeded):
            headers["Retry-After"] = str(exc.retry_after)

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "type": exc.error_type,
                    "message": exc.message,
                    "request_id": request_id,
                    "code": exc.status_code,
                }
            },
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Handle FastAPI/Starlette HTTP exceptions."""
        request_id = getattr(request.state, "request_id", None)

        if isinstance(exc.detail, dict) and "error" in exc.detail:
            content = exc.detail
            if content["error"].get("request_id") is None:
                content["error"]["request_id"] = request_id
            return JSONResponse(status_code=exc.status_code, content=content)

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "type": "http_error",
                    "message": str(exc.detail) if exc.detail else "An error occurred.",
                    "request_id": request_id,
                    "code": exc.status_code,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Handle Pydantic validation errors as 400 Bad Request."""
        request_id = getattr(request.state, "request_id", None)

        errors = exc.errors()
        messages = []
        for error in errors:
            loc = " → ".join(str(l) for l in error.get("loc", []))
            msg = error.get("msg", "Invalid value")
            messages.append(f"{loc}: {msg}")

        message = "; ".join(messages) if messages else "Invalid request body."

        logger.warning("request_validation_error", errors=errors)

        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "invalid_request",
                    "message": message,
                    "request_id": request_id,
                    "code": 400,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all for unhandled exceptions — return 500 with error envelope."""
        request_id = getattr(request.state, "request_id", None)

        logger.error(
            "unhandled_exception",
            error_type=type(exc).__name__,
            message=str(exc),
            exc_info=True,
        )

        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "type": "internal_error",
                    "message": "An unexpected error occurred.",
                    "request_id": request_id,
                    "code": 500,
                }
            },
        )
