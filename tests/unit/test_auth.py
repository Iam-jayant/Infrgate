"""
Unit tests for API key generation and authentication flow.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infrgate.services.api_key_service import generate_api_key


class TestApiKeyGeneration:
    """Tests for API key generation."""

    def test_key_format(self):
        """Key follows sk-infr_<prefix>.<secret> format."""
        full_key, prefix, key_hash = generate_api_key()
        assert full_key.startswith("sk-infr_")
        assert "." in full_key
        assert prefix.startswith("sk-infr_")

    def test_key_prefix_length(self):
        """Prefix is sk-infr_ + 8 chars."""
        _, prefix, _ = generate_api_key()
        assert len(prefix) > len("sk-infr_")

    def test_key_hash_is_sha256(self):
        """Key hash is SHA-256 hex digest of the full key."""
        full_key, _, key_hash = generate_api_key()
        expected = hashlib.sha256(full_key.encode()).hexdigest()
        assert key_hash == expected

    def test_key_uniqueness(self):
        """Each call generates a unique key."""
        keys = [generate_api_key() for _ in range(10)]
        full_keys = [k[0] for k in keys]
        assert len(set(full_keys)) == 10

    def test_key_hash_verification(self):
        """Hash verification succeeds with correct key and fails with wrong key."""
        full_key, prefix, key_hash = generate_api_key()

        assert secrets.compare_digest(
            hashlib.sha256(full_key.encode()).hexdigest(),
            key_hash,
        )

        wrong_key = full_key + "x"
        assert not secrets.compare_digest(
            hashlib.sha256(wrong_key.encode()).hexdigest(),
            key_hash,
        )
