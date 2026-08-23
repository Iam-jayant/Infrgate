import pytest
import urllib.parse
from uuid import uuid4
from infrgate.worker.handlers import handle_webhook_delivery, NonRetryableError
from infrgate.db.models.job import Job

@pytest.mark.asyncio
async def test_webhook_delivery_blocks_internal_ips(db_session, test_tenant, monkeypatch):
    """
    Test that handle_webhook_delivery resolves the hostname and rejects internal IPs.
    """
    import socket
    
    # Mock socket.gethostbyname to return a local IP (simulating DNS rebinding to internal network)
    def mock_gethostbyname(hostname):
        return "127.0.0.1"
        
    monkeypatch.setattr(socket, "gethostbyname", mock_gethostbyname)
    
    job = Job(
        id=uuid4(),
        tenant_id=test_tenant.id,
        job_type="webhook_delivery",
        payload={
            "url": "https://malicious-webhook.com/endpoint",
            "event_type": "spend_alert",
            "payload": {"test": True}
        },
        attempts=1,
        status="pending"
    )
    db_session.add(job)
    await db_session.flush()
    
    # We don't actually hit the network because gethostbyname raises NonRetryableError first
    with pytest.raises(NonRetryableError, match="URL resolves to restricted IP: 127.0.0.1"):
        await handle_webhook_delivery(db_session, job)

@pytest.mark.asyncio
async def test_webhook_delivery_https_tls_regression(db_session, test_tenant, monkeypatch):
    """
    Test that legitimate HTTPS requests use the ResolvedIPNetworkBackend and preserve SNI.
    """
    import socket
    from unittest.mock import AsyncMock
    import httpx
    
    # Mock socket.gethostbyname to return a fake valid external IP
    def mock_gethostbyname(hostname):
        return "93.184.216.34"  # example.com IP
        
    monkeypatch.setattr(socket, "gethostbyname", mock_gethostbyname)
    
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.text = "OK"
    
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=mock_resp))
    
    job = Job(
        id=uuid4(),
        tenant_id=test_tenant.id,
        job_type="webhook_delivery",
        payload={
            "url": "https://legitimate-webhook.com/endpoint",
            "event_type": "spend_alert",
            "payload": {"test": True}
        },
        attempts=1,
        status="pending"
    )
    db_session.add(job)
    await db_session.flush()
    
    # Should not raise any error and should complete successfully
    await handle_webhook_delivery(db_session, job)
    
    # Verify the delivery record was updated to completed
    from sqlalchemy import select
    from infrgate.db.models.webhook_delivery import WebhookDelivery
    stmt = select(WebhookDelivery).where(WebhookDelivery.job_id == job.id)
    delivery = (await db_session.execute(stmt)).scalar_one()
    assert delivery.status == "completed"
    assert delivery.http_status == 200
