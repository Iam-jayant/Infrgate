import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from infrgate.services.job_service import enqueue_spend_alert

@pytest.mark.asyncio
async def test_spend_alert_enqueue(db_session: AsyncSession, test_tenant):
    job_enqueued = await enqueue_spend_alert(
        db_session,
        tenant_id=test_tenant.id,
        threshold="50",
        billing_period="2026-08"
    )
    assert job_enqueued is True
    
    # Check idempotency
    job_enqueued_2 = await enqueue_spend_alert(
        db_session,
        tenant_id=test_tenant.id,
        threshold="50",
        billing_period="2026-08"
    )
    assert job_enqueued_2 is False

@pytest.mark.asyncio
async def test_webhook_delivery_400_no_retry(db_session: AsyncSession, test_tenant):
    from infrgate.worker.handlers import handle_webhook_delivery, NonRetryableError
    from infrgate.db.models.job import Job
    from unittest.mock import patch, AsyncMock
    import uuid
    from datetime import datetime, timezone
    
    job = Job(
        id=uuid.uuid4(),
        job_type="webhook_delivery",
        tenant_id=test_tenant.id,
        payload={"url": "http://test.com", "event_type": "test", "payload": {"k": "v"}},
        status="pending",
        attempts=1,
        max_attempts=5,
        scheduled_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(job)
    await db_session.commit()
    
    with patch("httpx.AsyncClient.post") as mock_post, patch("socket.gethostbyname") as mock_dns:
        mock_dns.return_value = "8.8.8.8"
        mock_resp = AsyncMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"
        mock_post.return_value = mock_resp
        
        with pytest.raises(NonRetryableError):
            await handle_webhook_delivery(db_session, job)

@pytest.mark.asyncio
async def test_webhook_delivery_500_retry(db_session: AsyncSession, test_tenant):
    from infrgate.worker.handlers import handle_webhook_delivery
    from infrgate.db.models.job import Job
    from unittest.mock import patch, AsyncMock
    import uuid
    from datetime import datetime, timezone
    
    job = Job(
        id=uuid.uuid4(),
        job_type="webhook_delivery",
        tenant_id=test_tenant.id,
        payload={"url": "http://test.com", "event_type": "test", "payload": {"k": "v"}},
        status="pending",
        attempts=1,
        max_attempts=5,
        scheduled_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(job)
    await db_session.commit()
    
    with patch("httpx.AsyncClient.post") as mock_post, patch("socket.gethostbyname") as mock_dns:
        mock_dns.return_value = "8.8.8.8"
        mock_resp = AsyncMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp
        
        # 500 should raise generic Exception or ValueError
        with pytest.raises(ValueError, match="Webhook returned server error"):
            await handle_webhook_delivery(db_session, job)
