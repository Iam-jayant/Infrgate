"""
Service for background job operations.
Spec reference: 12-background-jobs.md
"""

import uuid
from typing import Any
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from infrgate.db.models.job import Job

logger = structlog.get_logger()

async def enqueue_job(
    session: AsyncSession,
    job_type: str,
    tenant_id: uuid.UUID,
    payload: dict[str, Any],
    max_attempts: int = 5,
) -> uuid.UUID:
    """Enqueue a generic job."""
    job = Job(
        job_type=job_type,
        tenant_id=tenant_id,
        payload=payload,
        max_attempts=max_attempts,
    )
    session.add(job)
    await session.flush()
    logger.info("job_enqueued", job_id=str(job.id), job_type=job_type)
    return job.id


async def enqueue_spend_alert(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    threshold: str,
    billing_period: str,
) -> bool:
    """
    Enqueue a spend alert if it hasn't been fired yet for this period.
    Uses ON CONFLICT DO NOTHING against the partial unique index.
    
    """
    stmt = insert(Job).values(
        job_type="spend_alert",
        tenant_id=tenant_id,
        payload={
            "threshold": threshold,
            "billing_period": billing_period,
        },
        max_attempts=3,
    ).on_conflict_do_nothing(
        constraint="idx_jobs_spend_alert_idempotency"
    ).returning(Job.id)
    
    result = await session.execute(stmt)
    job_id = result.scalar_one_or_none()
    
    if job_id:
        logger.info(
            "spend_alert_enqueued", 
            job_id=str(job_id), 
            tenant_id=str(tenant_id), 
            threshold=threshold,
            billing_period=billing_period
        )
        return True
    
    return False
