"""
Admin API router — tenant management, API keys, and usage queries.
"""

from __future__ import annotations

from fastapi import APIRouter

from infrgate.api.admin.api_keys import router as keys_router
from infrgate.api.admin.tenants import router as tenants_router
from infrgate.api.admin.usage import router as usage_router
from infrgate.api.admin.providers import router as providers_router
from infrgate.api.admin.routing import router as routing_router

admin_router = APIRouter(prefix="/admin", tags=["admin"])
admin_router.include_router(tenants_router)
admin_router.include_router(keys_router)
admin_router.include_router(usage_router)
admin_router.include_router(providers_router)
admin_router.include_router(routing_router)
