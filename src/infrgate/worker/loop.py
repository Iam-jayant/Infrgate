"""
Worker loop logic: dequeue, execute, complete, fail, stuck recovery.
Spec reference: 12-background-jobs.md §2.1
"""

import asyncio
import uuid
import structlog
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from infrgate.db.models.job import Job
from infrgate.worker.handlers import get_handler, NonRetryableError

logger = structlog.get_logger()

class WorkerLoop:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.session_factory = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

    async def run(self):
        # Start the stuck recovery loop in the background
        asyncio.create_task(self.recover_stuck_jobs_loop())
        
        while True:
            try:
                job = await self.dequeue()
                if job:
                    await self.process_job(job)
                else:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("worker_loop_error", error=str(e))
                await asyncio.sleep(5.0)

    async def dequeue(self) -> Job | None:
        """Fetch next pending job using FOR UPDATE SKIP LOCKED."""
        async with self.session_factory() as session:
            stmt = (
                select(Job)
                .where(
                    (Job.status == "pending") &
                    (Job.scheduled_at <= datetime.now(timezone.utc))
                )
                .order_by(Job.scheduled_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            
            result = await session.execute(stmt)
            job = result.scalar_one_or_none()
            
            if job:
                job.status = "processing"
                job.started_at = datetime.now(timezone.utc)
                job.attempts += 1
                await session.commit()
                return job
                
            return None

    async def process_job(self, job: Job):
        logger.info("processing_job", job_id=str(job.id), job_type=job.job_type)
        
        import time
        start_time = time.monotonic()
        
        try:
            handler = get_handler(job.job_type)
            if not handler:
                raise ValueError(f"Unknown job type: {job.job_type}")
                
            async with self.session_factory() as session:
                await handler(session, job)
                await session.commit()
                
            await self.complete_job(job.id)
            logger.info("job_completed", job_id=str(job.id))
            
            from infrgate.metrics import JOBS_PROCESSED_TOTAL, JOB_DURATION
            JOBS_PROCESSED_TOTAL.labels(job_type=job.job_type, status="success").inc()
            JOB_DURATION.labels(job_type=job.job_type).observe(time.monotonic() - start_time)
            
        except NonRetryableError as e:
            logger.error("job_failed_permanent", job_id=str(job.id), error=str(e))
            await self.fail_job(job.id, str(e), permanent=True)
            from infrgate.metrics import JOBS_PROCESSED_TOTAL, JOB_DURATION
            JOBS_PROCESSED_TOTAL.labels(job_type=job.job_type, status="permanent_failure").inc()
            JOB_DURATION.labels(job_type=job.job_type).observe(time.monotonic() - start_time)
        except Exception as e:
            logger.error("job_failed", job_id=str(job.id), error=str(e))
            await self.fail_job(job.id, str(e))
            from infrgate.metrics import JOBS_PROCESSED_TOTAL, JOB_DURATION
            JOBS_PROCESSED_TOTAL.labels(job_type=job.job_type, status="retryable_failure").inc()
            JOB_DURATION.labels(job_type=job.job_type).observe(time.monotonic() - start_time)

    async def complete_job(self, job_id: uuid.UUID):
        async with self.session_factory() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status="completed",
                    completed_at=datetime.now(timezone.utc)
                )
            )
            await session.commit()

    async def fail_job(self, job_id: uuid.UUID, error_message: str, permanent: bool = False):
        async with self.session_factory() as session:
            job = await session.get(Job, job_id)
            if not job:
                return
                
            if permanent or job.attempts >= job.max_attempts:
                job.status = "dead_letter"
            else:
                job.status = "pending"
                # Exponential backoff
                delay = 2 ** job.attempts
                job.scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=delay)
                
            job.error_message = error_message
            await session.commit()

    async def recover_stuck_jobs_loop(self):
        """Periodically find jobs stuck in processing and reset them to pending."""
        while True:
            try:
                await self.recover_stuck_jobs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("stuck_job_recovery_error", error=str(e))
                
            await asyncio.sleep(60)
            
    async def recover_stuck_jobs(self):
        timeout = datetime.now(timezone.utc) - timedelta(minutes=15)
        
        async with self.session_factory() as session:
            result = await session.execute(
                update(Job)
                .where(
                    (Job.status == "processing") &
                    (Job.started_at < timeout)
                )
                .values(
                    status="pending",
                    error_message="Recovered from stuck processing state"
                )
                .returning(Job.id)
            )
            recovered = result.scalars().all()
            if recovered:
                logger.warning("stuck_jobs_recovered", count=len(recovered), job_ids=[str(id) for id in recovered])
            await session.commit()
