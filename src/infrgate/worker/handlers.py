"""
Job handlers.
"""

import httpcore
import httpx
import structlog
from typing import Callable, Awaitable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone
import urllib.parse
import socket
import ipaddress

from infrgate.db.models.job import Job
from infrgate.db.models.webhook_delivery import WebhookDelivery
from infrgate.db.models.tenant import Tenant

logger = structlog.get_logger()

# Type for job handler functions
JobHandler = Callable[[AsyncSession, Job], Awaitable[None]]

_HANDLERS: dict[str, JobHandler] = {}

class NonRetryableError(Exception):
    pass

def get_handler(job_type: str) -> JobHandler | None:
    return _HANDLERS.get(job_type)

def register_handler(job_type: str):
    def decorator(func: JobHandler):
        _HANDLERS[job_type] = func
        return func
    return decorator


@register_handler("usage_aggregation")
async def handle_usage_aggregation(session: AsyncSession, job: Job):
    """Aggregate usage from the ledger into tenant current_spend_cents."""
    # Not required for phase 3, but defined as a stub
    pass


@register_handler("spend_alert")
async def handle_spend_alert(session: AsyncSession, job: Job):
    """
    Process a spend alert job.
    Spec reference: 10-usage-accounting.md §4
    """
    tenant_id = job.tenant_id
    payload = job.payload
    threshold = payload.get("threshold", "0")
    billing_period = payload.get("billing_period")
    
    tenant = await session.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError("Tenant not found")
        
    webhook_url = tenant.config.get("webhook_url") if tenant.config else None
    
    if not webhook_url:
        logger.info("skip_spend_alert_no_webhook", tenant_id=str(tenant_id))
        return
        
    webhook_payload = {
        "event_type": "spend_alert",
        "tenant_id": str(tenant_id),
        "threshold": threshold,
        "billing_period": billing_period,
        "current_spend_cents": tenant.current_spend_cents,
        "spend_cap_cents": tenant.spend_cap_cents,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # Enqueue a webhook delivery job
    from infrgate.services.job_service import enqueue_job
    await enqueue_job(
        session,
        job_type="webhook_delivery",
        tenant_id=tenant_id,
        payload={
            "url": webhook_url,
            "event_type": "spend_alert",
            "payload": webhook_payload,
            "parent_job_id": str(job.id)
        }
    )


class ResolvedIPNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, original_backend: httpcore.AsyncNetworkBackend, resolved_ip: str):
        self._original = original_backend
        self._resolved_ip = resolved_ip

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        **kwargs,
    ) -> httpcore.AsyncNetworkStream:
        # Swap the hostname for the resolved IP, but httpx will still use the original
        # hostname for TLS SNI and the Host header.
        return await self._original.connect_tcp(
            self._resolved_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            **kwargs,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        **kwargs,
    ) -> httpcore.AsyncNetworkStream:
        return await self._original.connect_unix_socket(path, timeout=timeout, **kwargs)
        
    async def sleep(self, seconds: float) -> None:
        return await self._original.sleep(seconds)

@register_handler("webhook_delivery")
async def handle_webhook_delivery(session: AsyncSession, job: Job):
    """
    Deliver a webhook.
    Spec reference: 12-background-jobs.md §3.1
    """
    payload = job.payload
    url = payload.get("url")
    event_type = payload.get("event_type")
    webhook_payload = payload.get("payload")
    
    if not all([url, event_type, webhook_payload]):
        raise ValueError("Invalid webhook payload")
        
    # Create or update webhook delivery record
    stmt = select(WebhookDelivery).where(WebhookDelivery.job_id == job.id)
    result = await session.execute(stmt)
    delivery = result.scalar_one_or_none()
    
    if not delivery:
        delivery = WebhookDelivery(
            tenant_id=job.tenant_id,
            job_id=job.id,
            event_type=event_type,
            url=url,
            payload=webhook_payload,
        )
        session.add(delivery)
        await session.flush()
        
    delivery.attempt = job.attempts
    
    try:
        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.scheme not in ("http", "https"):
            raise NonRetryableError("Invalid URL scheme")
        
        hostname = parsed_url.hostname
        if not hostname:
            raise NonRetryableError("Missing hostname")
            
        try:
            import asyncio
            ip_str = await asyncio.to_thread(socket.gethostbyname, hostname)
            ip_obj = ipaddress.ip_address(ip_str)
            if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_reserved or not ip_obj.is_global:
                raise NonRetryableError(f"URL resolves to restricted IP: {ip_str}")
        except socket.gaierror:
            raise NonRetryableError(f"Could not resolve hostname: {hostname}")

        headers = {
            "X-InfrGate-Event": event_type,
            "X-InfrGate-Delivery-ID": str(delivery.id)
        }
        
        backend = ResolvedIPNetworkBackend(httpcore.AnyIOBackend(), ip_str)
        transport = httpx.AsyncHTTPTransport()
        transport._pool._network_backend = backend
        
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            resp = await client.post(
                url, 
                json=webhook_payload,
                headers=headers,
                timeout=10.0
            )
            delivery.http_status = resp.status_code
            delivery.response_body = resp.text[:2000]
            
            if resp.status_code >= 500:
                delivery.status = "failed"
                from infrgate.metrics import WEBHOOKS_DELIVERED_TOTAL
                WEBHOOKS_DELIVERED_TOTAL.labels(event_type=event_type, status="server_error").inc()
                raise ValueError(f"Webhook returned server error {resp.status_code}")
            elif resp.status_code >= 400:
                delivery.status = "failed"
                from infrgate.metrics import WEBHOOKS_DELIVERED_TOTAL
                WEBHOOKS_DELIVERED_TOTAL.labels(event_type=event_type, status="client_error").inc()
                raise NonRetryableError(f"Webhook returned client error {resp.status_code}")
                
            delivery.status = "completed"
            delivery.completed_at = datetime.now(timezone.utc)
            from infrgate.metrics import WEBHOOKS_DELIVERED_TOTAL
            WEBHOOKS_DELIVERED_TOTAL.labels(event_type=event_type, status="success").inc()
            
    except NonRetryableError:
        raise
    except Exception as e:
        delivery.status = "failed"
        delivery.response_body = str(e)
        from infrgate.metrics import WEBHOOKS_DELIVERED_TOTAL
        WEBHOOKS_DELIVERED_TOTAL.labels(event_type=event_type, status="network_error").inc()
        raise
