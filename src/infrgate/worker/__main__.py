"""
Entry point for the background worker.
"""

import asyncio
import logging
import structlog

from infrgate.config import get_settings
from sqlalchemy.ext.asyncio import create_async_engine
from infrgate.worker.loop import WorkerLoop

logger = structlog.get_logger()

async def main():
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    
    worker = WorkerLoop(engine)
    logger.info("starting_worker")
    
    try:
        await worker.run()
    except asyncio.CancelledError:
        logger.info("worker_stopped")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
        ]
    )
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
