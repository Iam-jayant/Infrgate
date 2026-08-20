"""
Database engine, session factory, and FastAPI dependency.

Provides async SQLAlchemy engine with connection pooling and a
session-scoped dependency for request-level database access.
"""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency that yields an async database session.

    The session is scoped to the request lifecycle and automatically
    closed after the request completes. Transactions are NOT auto-committed;
    service functions must call ``await session.commit()`` explicitly.
    """
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
