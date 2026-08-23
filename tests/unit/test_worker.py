import pytest
from infrgate.worker.handlers import get_handler, register_handler

@pytest.mark.asyncio
async def test_job_handler_registration():
    @register_handler("test_job")
    async def dummy_handler(session, job):
        pass
        
    handler = get_handler("test_job")
    assert handler is not None
