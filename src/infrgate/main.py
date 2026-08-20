"""
InfrGate — FastAPI application factory and entry point.

Creates the FastAPI application with all routers, middleware, and
lifecycle management (database, Redis, HTTP client).

Spec reference: 01-system-overview.md §5.1
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from infrgate.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle: startup and shutdown resources."""
    settings = get_settings()

    # ── Startup ───────────────────────────────────────────────────────────

    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    redis_pool = aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        max_connections=20,
    )

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=5.0,
            read=30.0,
            write=10.0,
            pool=10.0,
        ),
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
        ),
        follow_redirects=True,
    )

    app.state.db_engine = engine
    app.state.db_session_factory = session_factory
    app.state.redis = redis_pool
    app.state.http_client = http_client
    app.state.settings = settings

    from infrgate.logging import configure_logging

    configure_logging(settings.LOG_LEVEL)

    from infrgate.providers.registry import ProviderRegistry
    from infrgate.providers.gemini import GeminiAdapter
    from infrgate.providers.openai import OpenAIAdapter
    from infrgate.providers.fake import FakeAdapter

    registry = ProviderRegistry()
    registry.register(GeminiAdapter(api_key=settings.GEMINI_API_KEY, http_client=http_client))
    registry.register(OpenAIAdapter(api_key=settings.OPENAI_API_KEY, http_client=http_client))
    registry.register(FakeAdapter())

    app.state.provider_registry = registry

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    await http_client.aclose()
    await redis_pool.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="InfrGate",
        description="Intelligent inference control plane for LLM providers.",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── Middleware (outermost first) ──────────────────────────────────────
    from infrgate.middleware.request_id import RequestIDMiddleware

    app.add_middleware(RequestIDMiddleware)

    # ── Exception handlers ───────────────────────────────────────────────
    from infrgate.middleware.error_handler import register_error_handlers

    register_error_handlers(app)

    # ── Routers ──────────────────────────────────────────────────────────
    from infrgate.api.admin import admin_router
    from infrgate.api.v1 import v1_router

    app.include_router(v1_router)
    app.include_router(admin_router)

    return app


def run() -> None:
    """Run the application with Uvicorn (CLI entry point)."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "infrgate.main:create_app",
        factory=True,
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
        reload=False,
    )
