"""
Chat completion schemas — OpenAI-compatible request/response models.

Spec reference: 02-api-design.md §3.1
"""

from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A single message in the conversation."""

    role: Literal["system", "user", "assistant"] = Field(
        ..., description="Message role"
    )
    content: str = Field(..., description="Message content")


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request."""

    model: str = Field(..., description="Model identifier")
    messages: list[ChatMessage] = Field(
        ..., min_length=1, description="Conversation messages"
    )
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    stream: bool = Field(default=False, description="Enable SSE streaming (Phase 3)")
    n: int = Field(default=1, ge=1, le=1, description="Number of completions (only 1 supported)")
    stop: str | list[str] | None = Field(default=None)


class UsageInfo(BaseModel):
    """Token usage statistics."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatChoice(BaseModel):
    """A single completion choice."""

    index: int
    message: ChatMessage
    finish_reason: str | None


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response."""

    id: str = Field(description="Unique completion ID")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[ChatChoice]
    usage: UsageInfo


def build_completion_response(
    request_id: str,
    model: str,
    content: str,
    finish_reason: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> ChatCompletionResponse:
    """Build a ChatCompletionResponse from provider response data."""
    return ChatCompletionResponse(
        id=f"chatcmpl-{request_id}",
        model=model,
        choices=[
            ChatChoice(
                index=0,
                message=ChatMessage(role="assistant", content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=UsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
    )
