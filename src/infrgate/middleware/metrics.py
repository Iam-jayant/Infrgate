import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from infrgate.metrics import REQUESTS_TOTAL, REQUEST_DURATION, ACTIVE_REQUESTS

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        ACTIVE_REQUESTS.inc()
        start_time = time.time()
        
        try:
            response = await call_next(request)
            
            # Record metrics after response
            duration = time.time() - start_time
            
            # Try to get tenant_id and model from request scope/state if they exist
            # For unauthenticated endpoints, tenant won't exist.
            tenant_id = "unknown"
            model = "unknown"
            
            if hasattr(request.state, "tenant_id"):
                tenant_id = request.state.tenant_id
                
            if hasattr(request.state, "requested_model"):
                model = request.state.requested_model
                
            status = "success" if 200 <= response.status_code < 400 else "error"
            
            # Only record specific API paths, ignore metrics for /health and /metrics themselves
            if request.url.path.startswith("/v1/"):
                REQUESTS_TOTAL.labels(tenant=tenant_id, model=model, status=status).inc()
                REQUEST_DURATION.labels(model=model).observe(duration)
                
            return response
        finally:
            ACTIVE_REQUESTS.dec()
