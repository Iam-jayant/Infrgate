"""
Admin API for Routing Decisions.
"""

import json

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from infrgate.auth.dependencies import verify_admin

router = APIRouter(
    prefix="/routing",
    tags=["admin-routing"],
    dependencies=[Depends(verify_admin)],
)


@router.get("/decisions")
async def list_routing_decisions(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    tenant_id: str | None = None,
):
    """List recent routing decisions."""
    redis = request.app.state.redis
    
    # Read from redis list
    key = "infrgate:routing_decisions"
    # We fetch up to limit, but if there's a filter, we might need to fetch more and filter.
    # To keep it simple and efficient, we fetch limit * 5 to have enough candidates if filtering by tenant.
    fetch_limit = limit * 5 if tenant_id else limit
    
    raw_decisions = await redis.lrange(key, 0, fetch_limit - 1)
    
    decisions = []
    for raw in raw_decisions:
        try:
            decision = json.loads(raw)
            if tenant_id and decision.get("tenant_id") != tenant_id:
                continue
            
            decisions.append(decision)
            if len(decisions) >= limit:
                break
        except Exception:
            pass

    return {"decisions": decisions}
