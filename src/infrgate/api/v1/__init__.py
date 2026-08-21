"""
V1 API router — client-facing inference endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter

from infrgate.api.v1.chat_completions import router as chat_router

v1_router = APIRouter(prefix="/v1", tags=["inference"])
v1_router.include_router(chat_router)
