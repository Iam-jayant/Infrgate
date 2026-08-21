"""
Unit tests for policy enforcement — model authorization, spend cap, plan resolution.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from infrgate.auth.policy import (
    check_model_authorization,
    check_spend_cap,
    resolve_allowed_models,
    resolve_rpm_limit,
    resolve_tpm_limit,
)
from infrgate.exceptions import ModelNotAllowed, SpendCapExceeded


def _make_tenant(
    plan: str = "standard",
    config: dict | None = None,
    spend_cap_cents: int | None = 10_000,
    current_spend_cents: int = 0,
) -> MagicMock:
    """Create a mock tenant for policy tests."""
    tenant = MagicMock()
    tenant.plan = plan
    tenant.config = config or {}
    tenant.spend_cap_cents = spend_cap_cents
    tenant.current_spend_cents = current_spend_cents
    return tenant


class TestModelAuthorization:
    """Tests for model authorization against tenant plans."""

    def test_allowed_model_passes(self):
        """Model in allowed list passes authorization."""
        tenant = _make_tenant(config={"allowed_models": ["gemini-2.0-flash"]})
        check_model_authorization(tenant, "gemini-2.0-flash")  # Should not raise

    def test_disallowed_model_raises(self):
        """Model not in allowed list raises ModelNotAllowed."""
        tenant = _make_tenant(config={"allowed_models": ["gemini-2.0-flash"]})
        with pytest.raises(ModelNotAllowed):
            check_model_authorization(tenant, "gpt-4o")

    def test_plan_default_models(self):
        """When no config override, plan defaults are used."""
        tenant = _make_tenant(plan="free", config={})
        models = resolve_allowed_models(tenant)
        assert "gemini-2.0-flash" in models

    def test_config_override_models(self):
        """Config override takes precedence over plan defaults."""
        tenant = _make_tenant(
            plan="free",
            config={"allowed_models": ["gemini-2.5-pro"]},
        )
        models = resolve_allowed_models(tenant)
        assert models == ["gemini-2.5-pro"]


class TestSpendCap:
    """Tests for spend cap enforcement."""

    def test_under_cap_passes(self):
        """Spend under cap passes check."""
        tenant = _make_tenant(spend_cap_cents=10_000, current_spend_cents=5_000)
        check_spend_cap(tenant)  # Should not raise

    def test_at_cap_raises(self):
        """Spend at cap raises SpendCapExceeded."""
        tenant = _make_tenant(spend_cap_cents=10_000, current_spend_cents=10_000)
        with pytest.raises(SpendCapExceeded):
            check_spend_cap(tenant)

    def test_over_cap_raises(self):
        """Spend over cap raises SpendCapExceeded."""
        tenant = _make_tenant(spend_cap_cents=10_000, current_spend_cents=15_000)
        with pytest.raises(SpendCapExceeded):
            check_spend_cap(tenant)

    def test_unlimited_cap_always_passes(self):
        """None spend cap (unlimited) always passes."""
        tenant = _make_tenant(spend_cap_cents=None, current_spend_cents=999_999)
        check_spend_cap(tenant)  # Should not raise


class TestPlanResolution:
    """Tests for RPM/TPM limit resolution."""

    def test_config_override_rpm(self):
        """Config rpm_limit overrides plan default."""
        tenant = _make_tenant(plan="free", config={"rpm_limit": 100})
        assert resolve_rpm_limit(tenant) == 100

    def test_plan_default_rpm(self):
        """Plan default RPM used when no config override."""
        tenant = _make_tenant(plan="free", config={})
        assert resolve_rpm_limit(tenant) == 10  # free plan default

    def test_standard_plan_rpm(self):
        """Standard plan has 60 RPM default."""
        tenant = _make_tenant(plan="standard", config={})
        assert resolve_rpm_limit(tenant) == 60

    def test_config_override_tpm(self):
        """Config tpm_limit overrides plan default."""
        tenant = _make_tenant(plan="free", config={"tpm_limit": 50_000})
        assert resolve_tpm_limit(tenant) == 50_000
