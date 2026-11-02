"""
Shared HTTP transport layer.

Provides both sync (`SyncTransport`) and async (`AsyncTransport`)
wrappers around httpx, handling:
  - Base URL and auth header injection
  - Automatic Idempotency-Key generation
  - Error response parsing → typed exceptions
  - Configurable retry with exponential backoff for 429 / 5xx
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Any, AsyncIterator, Iterator

import httpx

from infrgate_sdk.exceptions import (
    APITimeoutError,
    ConnectionError,
    InfrGateError,
    raise_for_status,
)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
_DEFAULT_MAX_RETRIES = 2
_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
_BASE_DELAY = 0.5
_MAX_DELAY = 8.0


def _make_headers(api_key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build default headers with auth."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "infrgate-sdk-python/0.1.0",
    }
    if extra:
        headers.update(extra)
    return headers


def _retry_delay(attempt: int) -> float:
    """Exponential backoff with jitter."""
    delay = min(_BASE_DELAY * (2**attempt), _MAX_DELAY)
    return random.uniform(0, delay)


def _should_retry(status_code: int, attempt: int, max_retries: int) -> bool:
    return status_code in _RETRY_STATUS_CODES and attempt < max_retries


# ─────────────────────────────────────────────────────────────────────────────
# Sync Transport
# ─────────────────────────────────────────────────────────────────────────────


class SyncTransport:
    """Synchronous HTTP transport using httpx."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: httpx.Timeout | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        default_headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max_retries
        self._headers = _make_headers(api_key, default_headers)
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=self._headers,
            timeout=timeout or _DEFAULT_TIMEOUT,
            follow_redirects=True,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        """Send a request and return parsed JSON, with retry on transient failures."""
        merged_headers = dict(self._headers)
        if headers:
            merged_headers.update(headers)
        if idempotent and "Idempotency-Key" not in merged_headers:
            merged_headers["Idempotency-Key"] = f"sdk-{uuid.uuid4()}"

        # Clean None values from params
        if params:
            params = {k: v for k, v in params.items() if v is not None}

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(
                    method, path, json=json, params=params, headers=merged_headers
                )
            except httpx.ConnectError as e:
                raise ConnectionError(
                    f"Failed to connect to {self.base_url}: {e}",
                    status_code=None,
                ) from e
            except httpx.TimeoutException as e:
                if attempt < self.max_retries:
                    time.sleep(_retry_delay(attempt))
                    continue
                raise APITimeoutError(
                    f"Request timed out: {e}",
                    status_code=None,
                ) from e

            if response.status_code < 400:
                return response.json()

            if _should_retry(response.status_code, attempt, self.max_retries):
                time.sleep(_retry_delay(attempt))
                continue

            # Terminal error — raise typed exception
            try:
                body = response.json()
            except Exception:
                body = None
            raise_for_status(
                response.status_code,
                body,
                dict(response.headers),
            )

        # Should not reach here, but just in case
        raise InfrGateError("Max retries exhausted")

    def stream_request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> Iterator[str]:
        """Send a streaming request and yield raw SSE lines."""
        merged_headers = dict(self._headers)
        merged_headers["Accept"] = "text/event-stream"
        if headers:
            merged_headers.update(headers)
        if "Idempotency-Key" not in merged_headers:
            merged_headers["Idempotency-Key"] = f"sdk-{uuid.uuid4()}"

        try:
            with self._client.stream(
                method, path, json=json, headers=merged_headers
            ) as response:
                if response.status_code >= 400:
                    # Read the full error body
                    error_body = b""
                    for chunk in response.iter_bytes():
                        error_body += chunk
                    import json as json_mod

                    try:
                        body = json_mod.loads(error_body)
                    except Exception:
                        body = None
                    raise_for_status(response.status_code, body, dict(response.headers))

                for line in response.iter_lines():
                    if line:
                        yield line
        except httpx.ConnectError as e:
            raise ConnectionError(
                f"Failed to connect to {self.base_url}: {e}",
                status_code=None,
            ) from e
        except httpx.TimeoutException as e:
            raise APITimeoutError(
                f"Stream timed out: {e}",
                status_code=None,
            ) from e

    def close(self) -> None:
        self._client.close()


# ─────────────────────────────────────────────────────────────────────────────
# Async Transport
# ─────────────────────────────────────────────────────────────────────────────


class AsyncTransport:
    """Asynchronous HTTP transport using httpx."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: httpx.Timeout | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        default_headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max_retries
        self._headers = _make_headers(api_key, default_headers)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            timeout=timeout or _DEFAULT_TIMEOUT,
            follow_redirects=True,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        """Send a request and return parsed JSON, with retry on transient failures."""
        import asyncio

        merged_headers = dict(self._headers)
        if headers:
            merged_headers.update(headers)
        if idempotent and "Idempotency-Key" not in merged_headers:
            merged_headers["Idempotency-Key"] = f"sdk-{uuid.uuid4()}"

        if params:
            params = {k: v for k, v in params.items() if v is not None}

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(
                    method, path, json=json, params=params, headers=merged_headers
                )
            except httpx.ConnectError as e:
                raise ConnectionError(
                    f"Failed to connect to {self.base_url}: {e}",
                    status_code=None,
                ) from e
            except httpx.TimeoutException as e:
                if attempt < self.max_retries:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                raise APITimeoutError(
                    f"Request timed out: {e}",
                    status_code=None,
                ) from e

            if response.status_code < 400:
                return response.json()

            if _should_retry(response.status_code, attempt, self.max_retries):
                import asyncio

                await asyncio.sleep(_retry_delay(attempt))
                continue

            try:
                body = response.json()
            except Exception:
                body = None
            raise_for_status(
                response.status_code,
                body,
                dict(response.headers),
            )

        raise InfrGateError("Max retries exhausted")

    async def stream_request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Send a streaming request and yield raw SSE lines."""
        merged_headers = dict(self._headers)
        merged_headers["Accept"] = "text/event-stream"
        if headers:
            merged_headers.update(headers)
        if "Idempotency-Key" not in merged_headers:
            merged_headers["Idempotency-Key"] = f"sdk-{uuid.uuid4()}"

        try:
            async with self._client.stream(
                method, path, json=json, headers=merged_headers
            ) as response:
                if response.status_code >= 400:
                    error_body = b""
                    async for chunk in response.aiter_bytes():
                        error_body += chunk
                    import json as json_mod

                    try:
                        body = json_mod.loads(error_body)
                    except Exception:
                        body = None
                    raise_for_status(response.status_code, body, dict(response.headers))

                async for line in response.aiter_lines():
                    if line:
                        yield line
        except httpx.ConnectError as e:
            raise ConnectionError(
                f"Failed to connect to {self.base_url}: {e}",
                status_code=None,
            ) from e
        except httpx.TimeoutException as e:
            raise APITimeoutError(
                f"Stream timed out: {e}",
                status_code=None,
            ) from e

    async def close(self) -> None:
        await self._client.aclose()
