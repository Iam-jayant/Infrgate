"""
InfrGate SDK — Python client for the InfrGate LLM inference gateway.

Quick start::

    from infrgate_sdk import InfrGateClient

    client = InfrGateClient(
        base_url="https://infrgate.onrender.com",
        api_key="your-api-key",
    )
    response = client.chat.completions.create(
        model="Qwen/Qwen2.5-72B-Instruct",
        messages=[{"role": "user", "content": "Hello!"}],
    )
"""

from infrgate_sdk.admin import AsyncInfrGateAdmin, InfrGateAdmin
from infrgate_sdk.client import AsyncInfrGateClient, InfrGateClient
from infrgate_sdk.exceptions import (
    APITimeoutError,
    AuthenticationError,
    ConflictError,
    ConnectionError,
    InfrGateError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)
from infrgate_sdk.models import (
    ApiKey,
    ApiKeyWithSecret,
    ChatChoice,
    ChatCompletionResponse,
    ChatMessage,
    PaginatedResponse,
    StreamChunk,
    StreamChoice,
    StreamDelta,
    Tenant,
    TenantConfig,
    UsageInfo,
    UsageRecord,
    UsageSummary,
    UsageSummaryByModel,
)
from infrgate_sdk.streaming import AsyncStream, Stream

__version__ = "0.1.0"

__all__ = [
    # Clients
    "InfrGateClient",
    "AsyncInfrGateClient",
    "InfrGateAdmin",
    "AsyncInfrGateAdmin",
    # Streaming
    "Stream",
    "AsyncStream",
    # Models — Chat
    "ChatMessage",
    "ChatChoice",
    "ChatCompletionResponse",
    "UsageInfo",
    # Models — Streaming
    "StreamDelta",
    "StreamChoice",
    "StreamChunk",
    # Models — Admin
    "Tenant",
    "TenantConfig",
    "ApiKey",
    "ApiKeyWithSecret",
    "UsageRecord",
    "UsageSummary",
    "UsageSummaryByModel",
    "PaginatedResponse",
    # Exceptions
    "InfrGateError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "RateLimitError",
    "ProviderError",
    "ProviderUnavailableError",
    "ProviderTimeoutError",
    "ConnectionError",
    "APITimeoutError",
    # Version
    "__version__",
]
