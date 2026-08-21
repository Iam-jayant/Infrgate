"""
Policy enforcement — model authorization, spend cap, and plan resolution.

These functions run AFTER authentication and BEFORE the provider call.
They enforce the "policy before provider call" invariant.

Spec reference: 04-authentication-tenancy.md §5, §6
"""

from __future__ import annotations

from infrgate.config import PLAN_DEFAULTS, get_settings
from infrgate.db.models.tenant import Tenant
from infrgate.exceptions import ModelNotAllowed, SpendCapExceeded


def resolve_allowed_models(tenant: Tenant) -> list[str]:
    """Resolve the effective model allowlist for a tenant."""
    config_models = tenant.config.get("allowed_models") if tenant.config else None
    if config_models:
        return config_models
    plan_config = PLAN_DEFAULTS.get(tenant.plan, {})
    return plan_config.get("models", [])


def resolve_rpm_limit(tenant: Tenant) -> int:
    """Resolve the effective RPM limit for a tenant."""
    if tenant.config:
        rpm = tenant.config.get("rpm_limit")
        if rpm is not None:
            return rpm
    plan_config = PLAN_DEFAULTS.get(tenant.plan, {})
    rpm = plan_config.get("rpm")
    if rpm is not None:
        return rpm
    return get_settings().DEFAULT_RPM


def resolve_tpm_limit(tenant: Tenant) -> int:
    """Resolve the effective TPM limit for a tenant."""
    if tenant.config:
        tpm = tenant.config.get("tpm_limit")
        if tpm is not None:
            return tpm
    plan_config = PLAN_DEFAULTS.get(tenant.plan, {})
    tpm = plan_config.get("tpm")
    if tpm is not None:
        return tpm
    return get_settings().DEFAULT_TPM


def check_model_authorization(tenant: Tenant, model: str) -> None:
    """
    Verify that the requested model is in the tenant's allowed list.

    Raises:
        ModelNotAllowed: if the model is not permitted for this tenant's plan
    """
    allowed = resolve_allowed_models(tenant)
    if allowed and model not in allowed:
        raise ModelNotAllowed(model=model, plan=tenant.plan)


def check_spend_cap(tenant: Tenant) -> None:
    """
    Verify that the tenant has not exceeded their spend cap.

    Raises:
        SpendCapExceeded: if current_spend_cents >= spend_cap_cents
    """
    if tenant.spend_cap_cents is None:
        return  # Unlimited
    if tenant.current_spend_cents >= tenant.spend_cap_cents:
        raise SpendCapExceeded()
