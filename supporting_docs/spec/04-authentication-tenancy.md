# 04 — Authentication & Tenancy

> API key lifecycle, authentication flow, tenant isolation, and plan model for InfrGate.

| Attribute | Value |
|---|---|
| **Status** | Final |
| **Phases** | Phase 1 (core), extended in later phases |
| **Audience** | All contributors |

---

## 1. Overview

InfrGate is a multi-tenant system. Every request must be authenticated and associated with exactly one tenant. The authentication mechanism is **API-key-based**, using a prefix + hash scheme for secure, fast lookups.

### Design goals

1. **No plaintext secrets stored** — only a SHA-256 hash of the key is persisted
2. **O(1) key lookup** — prefix-based index avoids scanning all keys
3. **Instant revocation** — setting `revoked_at` immediately blocks the key
4. **Tenant isolation** — a request can never access another tenant's data, keys, usage, or quotas
5. **Plan-based policy** — model access and rate limits are governed by the tenant's plan

---

## 2. API key format

```
sk-infr_<prefix>.<secret>
```

| Component | Length | Example | Description |
|---|---|---|---|
| `sk-infr_` | 8 chars | `sk-infr_` | Fixed scheme identifier |
| `<prefix>` | 8 chars | `aB3x9Kf2` | Random alphanumeric, stored in DB |
| `.` | 1 char | `.` | Delimiter |
| `<secret>` | 32 chars | `mN7pQ...` | Random alphanumeric, **never stored** |

**Total key length:** 49 characters

### 2.1 Key generation

```python
import secrets
import hashlib

def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, key_hash)."""
    prefix = secrets.token_urlsafe(6)[:8]       # 8-char alphanumeric prefix
    secret = secrets.token_urlsafe(24)[:32]      # 32-char secret
    full_key = f"sk-infr_{prefix}.{secret}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, f"sk-infr_{prefix}", key_hash
```

The full key is returned to the client **exactly once** at creation time. The system stores only `prefix` and `key_hash`.

---

## 3. Authentication flow

```text
┌──────────────────────────────────────────────────┐
│                 HTTP Request                      │
│  Authorization: Bearer sk-infr_aB3x.mN7pQ...    │
└──────────────────────────┬───────────────────────┘
                           │
                   ┌───────▼────────┐
                   │ Extract Bearer │
                   │    token       │
                   └───────┬────────┘
                           │
                  ┌────────▼─────────┐
                  │ Parse prefix     │    sk-infr_aB3x
                  │ (before '.')     │
                  └────────┬─────────┘
                           │
              ┌────────────▼──────────────┐
              │ SELECT * FROM api_keys    │
              │ WHERE prefix = ?          │
              │   AND revoked_at IS NULL  │
              └────────────┬──────────────┘
                           │
                  ┌────────▼─────────┐
          No ←────┤  Row found?      ├────→ Yes
          │       └──────────────────┘       │
          │                           ┌─────▼──────────┐
          │                           │ SHA-256 hash   │
          │                           │ full key       │
          │                           └─────┬──────────┘
          │                                 │
          │                        ┌────────▼──────────┐
          │                No ←────┤ Hash matches      ├────→ Yes
          │                │       │ stored key_hash?  │       │
          │                │       └───────────────────┘       │
          │                │                              ┌────▼──────────┐
          │                │                              │ Load tenant   │
          │                │                              │ by tenant_id  │
          │                │                              └────┬──────────┘
          │                │                                   │
          │                │                          ┌────────▼──────────┐
          │                │                   No ←───┤ Tenant status     ├───→ Yes
          │                │                   │      │ = 'active'?       │       │
          │                │                   │      └───────────────────┘       │
          │                │                   │                            ┌─────▼────────┐
          │                │                   │                            │ Set tenant   │
          │                │                   │                            │ context on   │
          │                │                   │                            │ request state│
          │                │                   │                            └─────┬────────┘
          │                │                   │                                  │
          ▼                ▼                   ▼                                  ▼
     401 invalid     401 invalid        403 tenant               ✅ Authenticated
     _api_key        _api_key           _suspended               Continue to policy
```

### 3.1 Implementation as FastAPI dependency

```python
async def get_current_tenant(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """
    FastAPI dependency that extracts and validates the API key,
    then returns the authenticated Tenant.
    """
    # 1. Extract bearer token
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, detail={"error": {"type": "authentication_required"}})

    # 2. Parse prefix
    prefix, _, secret = token.partition(".")
    if not prefix or not secret:
        raise HTTPException(401, detail={"error": {"type": "invalid_api_key"}})

    # 3. Lookup by prefix (active keys only)
    api_key = await db.execute(
        select(ApiKey)
        .where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None))
    )
    api_key = api_key.scalar_one_or_none()
    if not api_key:
        raise HTTPException(401, detail={"error": {"type": "invalid_api_key"}})

    # 4. Verify hash
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    if not secrets.compare_digest(key_hash, api_key.key_hash):
        raise HTTPException(401, detail={"error": {"type": "invalid_api_key"}})

    # 5. Load and validate tenant
    tenant = await db.get(Tenant, api_key.tenant_id)
    if tenant.status != "active":
        raise HTTPException(403, detail={"error": {"type": "tenant_suspended"}})

    return tenant
```

### 3.2 Security considerations

| Concern | Mitigation |
|---|---|
| **Timing attacks** | Use `secrets.compare_digest` for hash comparison |
| **Brute force** | 8-char prefix + 32-char secret = high entropy; rate limit auth failures |
| **Key leakage** | Key shown once at creation; never logged; hash only stored |
| **Revocation lag** | Immediate — query checks `revoked_at IS NULL` on every request |
| **DB compromise** | Only hashes stored; attacker cannot reconstruct keys |

---

## 4. Tenant model

### 4.1 Tenant fields

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `name` | string | Human-readable name |
| `plan` | string | `free`, `standard`, `enterprise` |
| `status` | string | `active`, `suspended` |
| `spend_cap_cents` | integer | Monthly spend cap in cents (null = unlimited) |
| `current_spend_cents` | integer | Running total of spend in current billing period |
| `config` | JSONB | Plan overrides (allowed models, rate limits) |

### 4.2 Tenant context

After authentication, the tenant is available on the request state throughout the request lifecycle:

```python
# In any handler or dependency
tenant: Tenant = request.state.tenant

# Accessible fields
tenant.id              # UUID — used for rate limit keys, usage records
tenant.plan            # str — determines default limits
tenant.config          # dict — model allowlist, limit overrides
tenant.spend_cap_cents # int — spend cap
tenant.current_spend_cents  # int — current spend
```

### 4.3 Tenant isolation rules

These rules are **invariants** — they must hold at all times:

1. **Data isolation:** Every database query that returns tenant-specific data MUST include a `WHERE tenant_id = ?` clause
2. **Key isolation:** An API key belongs to exactly one tenant; cross-tenant key usage is impossible by schema
3. **Rate limit isolation:** Rate limit counters are keyed by `tenant_id`; one tenant's traffic cannot affect another's limits
4. **Usage isolation:** Usage records are always associated with the authenticated tenant
5. **Config isolation:** A tenant's plan config (allowed models, limits) is resolved from their own record only

---

## 5. Plan model

### 5.1 Plan tiers

| Plan | RPM default | TPM default | Spend cap default | Models |
|---|---|---|---|---|
| `free` | 10 | 10,000 | $10 | `gemini-2.0-flash` |
| `standard` | 60 | 100,000 | $100 | All Gemini models |
| `enterprise` | 600 | 1,000,000 | Unlimited | All models |

### 5.2 Plan resolution

Rate limits and model access are resolved in order of precedence:

```text
1. Tenant config override (config JSONB)    ← highest precedence
2. Plan defaults                            ← fallback
3. System defaults                          ← lowest precedence
```

```python
def resolve_rpm_limit(tenant: Tenant) -> int:
    """Resolve effective RPM limit for a tenant."""
    # 1. Tenant-specific override
    if rpm := tenant.config.get("rpm_limit"):
        return rpm
    # 2. Plan default
    return PLAN_DEFAULTS[tenant.plan]["rpm"]

def resolve_allowed_models(tenant: Tenant) -> list[str]:
    """Resolve which models a tenant can access."""
    if models := tenant.config.get("allowed_models"):
        return models
    return PLAN_DEFAULTS[tenant.plan]["models"]
```

### 5.3 Model authorization

Before any provider call, the gateway checks that the requested model is in the tenant's allowed list:

```python
def authorize_model(tenant: Tenant, requested_model: str) -> None:
    allowed = resolve_allowed_models(tenant)
    if requested_model not in allowed:
        raise HTTPException(403, detail={
            "error": {
                "type": "model_not_allowed",
                "message": f"Model '{requested_model}' is not available on the '{tenant.plan}' plan."
            }
        })
```

---

## 6. Spend cap enforcement

### 6.1 Pre-flight check

Before making a provider call, the gateway checks whether the tenant has remaining budget:

```python
def check_spend_cap(tenant: Tenant) -> None:
    if tenant.spend_cap_cents is None:
        return  # Unlimited
    if tenant.current_spend_cents >= tenant.spend_cap_cents:
        raise HTTPException(403, detail={
            "error": {
                "type": "spend_cap_exceeded",
                "message": "Monthly spend cap exceeded."
            }
        })
```

### 6.2 Post-inference update

After recording usage, the tenant's `current_spend_cents` is updated:

```sql
UPDATE tenants
SET current_spend_cents = current_spend_cents + :cost_cents,
    updated_at = now()
WHERE id = :tenant_id;
```

### 6.3 Billing period reset

Spend counters reset at the start of each billing period (monthly). In Phase 1, this is a manual operation or cron job. In Phase 3, the background worker handles it automatically.

---

## 7. Admin authorization

### 7.1 Admin role (Phase 1 — simplified)

For Phase 1, admin endpoints are protected by a simple mechanism:

- A tenant with `plan = 'enterprise'` or a special `config.role = 'admin'` flag can access admin endpoints
- Alternatively, admin endpoints can require a separate `ADMIN_API_KEY` environment variable

### 7.2 Admin role (future enhancement)

In later phases, admin authorization may evolve to:
- Role-based access control (RBAC) with `admin`, `viewer`, `operator` roles
- Separate admin tokens with scoped permissions

This is explicitly deferred — see [13-future-work.md](13-future-work.md).

---

## References

- [02-api-design.md](02-api-design.md) — API key creation and revocation endpoints
- [03-data-model.md](03-data-model.md) — `tenants` and `api_keys` table schemas
- [05-rate-limiting.md](05-rate-limiting.md) — Rate limit enforcement using tenant context
- [10-usage-accounting.md](10-usage-accounting.md) — Spend cap updates after inference
