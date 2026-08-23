"""
Streaming schemas — internal streaming chunk representation and usage tracking.

Spec reference: 09-streaming.md
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from infrgate.schemas.chat import UsageInfo


@dataclass
class StreamChunk:
    """Internal representation of a streaming chunk, standardized to OpenAI format."""

    id: str
    model: str
    delta_role: str | None = None
    delta_content: str | None = None
    finish_reason: str | None = None
    usage: UsageInfo | None = None
    object: str = "chat.completion.chunk"
    created: int = field(default_factory=lambda: int(time.time()))

    def to_sse_event(self) -> str:
        """Serialize this chunk to an OpenAI-compatible Server-Sent Event."""
        choice = {
            "index": 0,
            "delta": {},
            "finish_reason": self.finish_reason,
        }

        if self.delta_role is not None:
            choice["delta"]["role"] = self.delta_role
        if self.delta_content is not None:
            choice["delta"]["content"] = self.delta_content

        # Per OpenAI spec, empty delta when finish_reason is present
        if self.finish_reason is not None and self.delta_role is None and self.delta_content is None:
            choice["delta"] = {}

        data: dict[str, Any] = {
            "id": f"chatcmpl-{self.id}",
            "object": self.object,
            "created": self.created,
            "model": self.model,
            "choices": [choice],
        }

        if self.usage is not None:
            # Usage might be a Pydantic model or dict, handle if it's a Pydantic model
            prompt_tokens = getattr(self.usage, "prompt_tokens", self.usage.get("prompt_tokens") if isinstance(self.usage, dict) else 0)
            completion_tokens = getattr(self.usage, "completion_tokens", self.usage.get("completion_tokens") if isinstance(self.usage, dict) else 0)
            total_tokens = getattr(self.usage, "total_tokens", self.usage.get("total_tokens") if isinstance(self.usage, dict) else 0)
            
            data["usage"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
            # OpenAI spec: if usage is included, choices should be empty array in that final usage chunk
            # but sometimes they are sent together depending on stream_options.
            # We'll stick to a standard shape.
            if not self.delta_content and not self.delta_role and not self.finish_reason:
                data["choices"] = []

        return f"data: {json.dumps(data)}\n\n"


class StreamUsageTracker:
    """
    Tracks token usage during a streaming completion.
    
    If the provider doesn't report final usage (e.g. client disconnect),
    falls back to estimating tokens from the accumulated content buffer.
    """

    def __init__(self, prompt_tokens: int = 0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = 0
        self.chunks_received = 0
        self.content_buffer: list[str] = []
        self.finish_reason: str | None = None
        self.start_time = time.monotonic()
        self.provider_reported_usage = False

    def update(self, chunk: StreamChunk) -> None:
        """Update tracker with data from a newly received chunk."""
        self.chunks_received += 1
        
        if chunk.delta_content:
            self.content_buffer.append(chunk.delta_content)
            
        if chunk.finish_reason:
            self.finish_reason = chunk.finish_reason
            
        if chunk.usage:
            # Usage reported by provider explicitly
            prompt_tokens = getattr(chunk.usage, "prompt_tokens", chunk.usage.get("prompt_tokens") if isinstance(chunk.usage, dict) else 0)
            completion_tokens = getattr(chunk.usage, "completion_tokens", chunk.usage.get("completion_tokens") if isinstance(chunk.usage, dict) else 0)
            
            # If the provider sends multiple usage chunks, we keep the latest.
            if prompt_tokens > 0:
                self.prompt_tokens = prompt_tokens
            if completion_tokens > 0:
                self.completion_tokens = completion_tokens
            self.provider_reported_usage = True

    def finalize(self, status: str = "completed") -> dict[str, Any]:
        """
        Finalize usage counting and return a dictionary suitable for record_usage.
        
        Args:
            status: 'completed', 'failed', or 'partial'
        """
        if not self.provider_reported_usage:
            # Fallback estimation
            content = "".join(self.content_buffer)
            self.completion_tokens = max(1, len(content) // 4) if content else 0
            
        latency_ms = int((time.monotonic() - self.start_time) * 1000)
            
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "provider_latency_ms": latency_ms,
            "finish_reason": self.finish_reason or "unknown",
            "status": status,
        }
