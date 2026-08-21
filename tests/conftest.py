"""
Shared test fixtures for InfrGate test suite.

Provides database sessions, Redis clients, FastAPI test app,
authenticated HTTP clients, and factory fixtures for tenants and API keys.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrgate.db.models import Base
from infrgate.db.models.api_key import ApiKey
from infrgate.db.models.tenant import Tenant
from infrgate.db.models.provider_config import ProviderConfig
from infrgate.config import get_settings
from infrgate.main import create_app


@pytest.fixture(scope="session", autouse=True)
def setup_env():
    os.environ["ADMIN_API_KEY"] = "test-admin-key"
    get_settings.cache_clear()


# ── Database fixtures (in-memory SQLite for unit tests) ──────────────────


@pytest_asyncio.fixture
async def db_engine():
    """Create an in-memory SQLite async engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Provide a transactional database session for each test."""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def app(db_engine, mock_redis, mock_registry):
    """Create a test FastAPI app with mocked dependencies."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    application = create_app()

    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    os.environ["ADMIN_API_KEY"] = "test-admin-key"
    application.state.db_engine = db_engine
    application.state.db_session_factory = session_factory
    application.state.redis = mock_redis
    application.state.provider_registry = mock_registry
    application.state.settings = type("Settings", (), {
        "ADMIN_API_KEY": "test-admin-key",
        "GEMINI_API_KEY": "test-gemini-key",
        "LOG_LEVEL": "WARNING",
    })()

    return application


@pytest_asyncio.fixture
async def client(app):
    """Create an async HTTP client for testing."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest_asyncio.fixture
async def setup_tenant(client):
    """Create a tenant and API key for testing."""
    resp = await client.post(
        "/admin/tenants",
        json={"name": "Test Corp", "plan": "standard", "spend_cap_cents": 10000},
        headers={"Authorization": "Bearer test-admin-key"},
    )
    assert resp.status_code == 201
    tenant = resp.json()

    resp = await client.post(
        f"/admin/tenants/{tenant['id']}/api-keys",
        json={"name": "test-key"},
        headers={"Authorization": "Bearer test-admin-key"},
    )
    assert resp.status_code == 201
    key_data = resp.json()

    return tenant, key_data["key"]


# ── Redis fixture (mock) ─────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    """Provide a mock Redis client for unit tests."""
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hincrby = AsyncMock(return_value=1)
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    
    pipe = MagicMock()
    redis.pipeline = MagicMock(return_value=pipe)

    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.execute = AsyncMock(return_value=[0, 0, True, True])

    return redis


# ── Test tenant & API key factories ──────────────────────────────────────


@pytest_asyncio.fixture
async def test_tenant(db_session: AsyncSession) -> Tenant:
    """Create a standard-plan test tenant."""
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Test Corp",
        plan="standard",
        status="active",
        spend_cap_cents=10_000,
        current_spend_cents=0,
        config={
            "allowed_models": ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro", "auto-model"],
            "rpm_limit": 60,
            "tpm_limit": 100_000,
        },
    )
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)
    return tenant


@pytest_asyncio.fixture
async def test_api_key(db_session: AsyncSession, test_tenant: Tenant) -> tuple[ApiKey, str]:
    """
    Create a test API key for the test tenant.

    Returns:
        Tuple of (ApiKey model, full key string).
    """
    prefix_random = secrets.token_urlsafe(8)[:8]
    secret = secrets.token_urlsafe(32)[:32]
    prefix = f"sk-infr_{prefix_random}"
    full_key = f"{prefix}.{secret}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()

    api_key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        name="test-key",
        prefix=prefix,
        key_hash=key_hash,
    )
    db_session.add(api_key)
    await db_session.commit()
    await db_session.refresh(api_key)
    return api_key, full_key


@pytest.fixture
def auth_headers(test_api_key) -> dict[str, str]:
    """Return Authorization headers for the test API key."""
    _, full_key = test_api_key
    return {"Authorization": f"Bearer {full_key}"}


@pytest_asyncio.fixture(autouse=True)
async def seed_test_provider_configs(db_session: AsyncSession):
    """Seed ProviderConfigs into the test database."""
    from infrgate.db.models.provider_config import ProviderConfig
    from sqlalchemy import select

    res = await db_session.execute(select(ProviderConfig))
    if not res.scalars().all():
        gemini = ProviderConfig(
            provider_name="gemini",
            display_name="Google Gemini",
            models=[{"model_id": "gemini-2.0-flash", "aliases": ["auto-model"]}],
            priority=100,
            cost_per_1k_tokens={},
            timeout_config={"total_timeout_s": 10.0},
            enabled=True,
        )
        openai = ProviderConfig(
            provider_name="openai",
            display_name="OpenAI",
            models=[{"model_id": "gpt-4o", "aliases": ["auto-model"]}],
            priority=110,
            cost_per_1k_tokens={},
            timeout_config={"total_timeout_s": 10.0},
            enabled=True,
        )
        db_session.add(gemini)
        db_session.add(openai)
        await db_session.commit()


# ── Mock Gemini adapter ──────────────────────────────────────────────────


@pytest.fixture
def mock_gemini_adapter():
    """Provide a mock Gemini adapter that returns predictable responses."""
    from infrgate.providers.base import ProviderResponse

    adapter = AsyncMock()
    adapter.provider_name = "gemini"
    adapter.supported_models = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"]
    adapter.complete = AsyncMock(
        return_value=ProviderResponse(
            content="This is a test response from Gemini.",
            model="gemini-2.0-flash",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=8,
            total_tokens=18,
            provider_latency_ms=150,
        )
    )
    return adapter


@pytest.fixture
def mock_registry(mock_gemini_adapter):
    """Provide a mock ProviderRegistry with mock adapters."""
    from infrgate.providers.registry import ProviderRegistry
    from unittest.mock import AsyncMock
    from infrgate.providers.base import ProviderResponse
    
    registry = ProviderRegistry()
    registry.register(mock_gemini_adapter)
    
    openai_adapter = AsyncMock()
    openai_adapter.provider_name = "openai"
    openai_adapter.supported_models = ["gpt-4o", "auto-model"]
    openai_adapter.complete = AsyncMock(
        return_value=ProviderResponse(
            content="This is a test response from OpenAI.",
            model="gpt-4o",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=8,
            total_tokens=18,
            provider_latency_ms=100,
        )
    )
    registry.register(openai_adapter)
    
    return registry
