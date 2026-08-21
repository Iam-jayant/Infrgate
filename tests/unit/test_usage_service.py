"""
Unit tests for the usage service — recording, idempotency, and cost calculation.
"""

from __future__ import annotations

import uuid

import pytest

from infrgate.providers.base import ProviderResponse
from infrgate.services.usage_service import calculate_cost_cents, estimate_prompt_tokens


class TestTokenEstimation:
    """Tests for prompt token estimation."""

    def test_basic_estimation(self):
        """Rough estimation of ~4 chars per token."""
        messages = [{"role": "user", "content": "Hello world"}]
        tokens = estimate_prompt_tokens(messages)
        assert tokens >= 1
        assert tokens <= 20  # "Hello world" = ~11 chars → ~3 tokens + overhead

    def test_empty_message(self):
        """Empty message returns at least 1 token."""
        messages = [{"role": "user", "content": ""}]
        tokens = estimate_prompt_tokens(messages)
        assert tokens >= 1

    def test_multiple_messages(self):
        """Multiple messages accumulate token count."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is the capital of France?"},
        ]
        tokens = estimate_prompt_tokens(messages)
        assert tokens > 5  # Should be reasonable for combined content


class TestCostCalculation:
    """Tests for cost calculation from token counts."""

    def test_free_model_zero_cost(self):
        """Free-tier Gemini models cost $0."""
        cost = calculate_cost_cents("gemini-2.0-flash", 1000, 500, {})
        assert cost == 0

    def test_unknown_model_zero_cost(self):
        """Unknown models default to zero cost."""
        cost = calculate_cost_cents("unknown-model", 1000, 500, {})
        assert cost == 0
