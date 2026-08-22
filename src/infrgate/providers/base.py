"""
Provider adapter base — abstract interface for all LLM providers.

Defines the contract that all provider adapters must implement.
The routing engine and reliability layer depend on this interface,
making them provider-agnostic.

Spec reference: 06-provider-adapters.md §2.1
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator

from infrgate.schemas.streaming import StreamChunk


@dataclass
class ProviderRequest:
    """Normalized request to a provider (OpenAI-compatible internal model)."""

    model: str
    messages: list[dict]
    temperature: float = 1.0
    max_tokens: int | None = None
    top_p: float = 1.0
    stop: str | list[str] | None = None
    stream: bool = False
    request_id: str = ""


@dataclass
class ProviderResponse:
    """Normalized response from a provider."""

    content: str
    model: str
    finish_reason: str  # "stop", "length", "content_filter"
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    provider_latency_ms: int
    raw_response: dict | None = None


class ProviderAdapter(ABC):
    """
    Abstract base class for all provider adapters.

    Each adapter translates between InfrGate's internal model
    (OpenAI-compatible) and a provider's native API.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique identifier for this provider (e.g., 'gemini', 'openai')."""
        ...

    @property
    @abstractmethod
    def supported_models(self) -> list[str]:
        """List of model IDs this provider supports."""
        ...

    @abstractmethod
    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """
        Execute a non-streaming chat completion.

        Args:
            request: Normalized provider request.

        Returns:
            Normalized provider response.

        Raises:
            ProviderError: On any provider failure.
        """
        ...

    @abstractmethod
    async def stream(self, request: ProviderRequest) -> AsyncIterator[StreamChunk]:
        """
        Execute a streaming chat completion.

        Args:
            request: Normalized provider request.

        Yields:
            StreamChunk objects representing chunks of the completion.

        Raises:
            ProviderError: On any provider failure.
        """
        ...
