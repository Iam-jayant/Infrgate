import pytest
import asyncio
from httpx import AsyncClient
from infrgate.db.models.usage_ledger import UsageLedger
from sqlalchemy import select
import uuid
import datetime
from infrgate.db.models.tenant import Tenant

@pytest.mark.asyncio
async def test_duplicate_key_concurrent_requests(client: AsyncClient, setup_tenant):
    tenant, api_key = setup_tenant
    idempotency_key = 'test-concurrent-123'

    async def make_request():
        return await client.post(
            '/v1/chat/completions',
            json={'model': 'gemini-2.0-flash', 'messages': [{'role': 'user', 'content': 'hi'}]},
            headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': idempotency_key},
        )

    req1, req2 = await asyncio.gather(make_request(), make_request())
    
    status_codes = {req1.status_code, req2.status_code}
    assert 200 in status_codes
    assert 409 in status_codes

@pytest.mark.asyncio
async def test_duplicate_key_replay_completed(client: AsyncClient, setup_tenant):
    tenant, api_key = setup_tenant
    idempotency_key = 'test-replay-123'

    res1 = await client.post(
        '/v1/chat/completions',
        json={'model': 'gemini-2.0-flash', 'messages': [{'role': 'user', 'content': 'hi'}]},
        headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': idempotency_key},
    )
    assert res1.status_code == 200

    res2 = await client.post(
        '/v1/chat/completions',
        json={'model': 'gemini-2.0-flash', 'messages': [{'role': 'user', 'content': 'hi'}]},
        headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': idempotency_key},
    )
    assert res2.status_code == 200
    assert res1.json()['id'] == res2.json()['id']

@pytest.mark.asyncio
async def test_duplicate_key_interleaved_cas_race(client: AsyncClient, setup_tenant, db_session):
    tenant, api_key = setup_tenant
    idempotency_key = 'test-cas-race-123'
    
    pending_row = UsageLedger(
        tenant_id=uuid.UUID(tenant['id']),
        idempotency_key=idempotency_key,
        model='gemini-2.0-flash',
        request_id=uuid.uuid4(),
        status='pending',
        claimed_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=100)
    )
    db_session.add(pending_row)
    await db_session.commit()
    
    res = await client.post(
        '/v1/chat/completions',
        json={'model': 'gemini-2.0-flash', 'messages': [{'role': 'user', 'content': 'hi'}]},
        headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': idempotency_key},
    )
    assert res.status_code == 200

@pytest.mark.asyncio
async def test_idempotency_early_policy_rejection_orphaned_row(client: AsyncClient, setup_tenant, db_session):
    tenant, api_key = setup_tenant
    
    db_tenant = await db_session.get(Tenant, uuid.UUID(tenant['id']))
    db_tenant.spend_cap_cents = 100
    db_tenant.current_spend_cents = 200
    await db_session.commit()
    
    idempotency_key = 'early-rejection-key-123'
    
    response1 = await client.post(
        '/v1/chat/completions',
        json={'model': 'gemini-2.0-flash', 'messages': [{'role': 'user', 'content': 'hi'}]},
        headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': idempotency_key},
    )
    assert response1.status_code == 403
    assert 'spend cap' in response1.json()['error']['message'].lower()
    
    response2 = await client.post(
        '/v1/chat/completions',
        json={'model': 'gemini-2.0-flash', 'messages': [{'role': 'user', 'content': 'hi'}]},
        headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': idempotency_key},
    )
    assert response2.status_code == 403
    assert 'spend cap' in response2.json()['error']['message'].lower()

@pytest.mark.asyncio
async def test_idempotency_benign_post_finalize_error_no_double_finalize(client: AsyncClient, setup_tenant, db_session, monkeypatch, caplog):
    tenant, api_key = setup_tenant
    
    # Mock TOKENS_TOTAL.labels().inc() to throw an exception
    import infrgate.metrics
    from unittest.mock import MagicMock
    mock_metric = MagicMock()
    mock_metric.inc.side_effect = Exception('Simulated metrics failure')
    mock_labels = MagicMock(return_value=mock_metric)
    monkeypatch.setattr(infrgate.metrics.TOKENS_TOTAL, 'labels', mock_labels)
    
    idempotency_key = 'benign-error-key-123'
    
    try:
        await client.post(
            '/v1/chat/completions',
            json={'model': 'gemini-2.0-flash', 'messages': [{'role': 'user', 'content': 'hi'}]},
            headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': idempotency_key},
        )
    except Exception:
        pass
    
    # The request should still return 500 because the exception bubbles up, OR wait...
    # Actually, in FastAPI, if an exception is raised before returning the response, it returns a 500.
    # The idempotency row should be 'completed' because it was successfully finalized before the error!
    
    # Check the ledger
    stmt = select(UsageLedger).where(UsageLedger.idempotency_key == idempotency_key)
    result = await db_session.execute(stmt)
    row = result.scalar_one_or_none()
    
    assert row is not None
    assert row.status == 'completed'
    
    # Ensure no 'idempotency_finalize_failed_on_error' log fired
    assert 'idempotency_finalize_failed_on_error' not in caplog.text

@pytest.mark.asyncio
async def test_idempotency_cancel_during_finalize_leaves_row_completed(setup_tenant, db_session, monkeypatch, caplog):
    # This test directly tests the FastAPI endpoint with an asyncio.Task cancellation,
    # so we'll use an async test client and explicitly cancel the request.
    from httpx import AsyncClient
    import asyncio
    from infrgate.main import app

    tenant, api_key = setup_tenant
    idempotency_key = 'cancel-finalize-key-123'
    
    # We want to mock finalize_request to sleep and then get cancelled.
    import infrgate.api.v1.chat_completions as cc
    original_finalize = cc.finalize_request
    
    finalize_started = asyncio.Event()

    async def mock_finalize(*args, **kwargs):
        finalize_started.set()
        # Sleep to keep it 'in flight' while we cancel the parent task
        await asyncio.sleep(0.5)
        # Call original to actually do the DB update, simulating completion
        await original_finalize(*args, **kwargs)

    monkeypatch.setattr(cc, 'finalize_request', mock_finalize)
    
    # We need to hit the endpoint via AsyncClient and cancel the request mid-flight.
    # But AsyncClient cancellation might not reliably cancel the ASGI task depending on the server.
    # A cleaner way to test shielding in Starlette/FastAPI without a real server is to just call
    # the endpoint function directly, or use a background task. 
    # Actually, httpx.AsyncClient with app=app will cancel the endpoint task if we cancel the client request task!
    
    async with AsyncClient(app=app, base_url='http://test') as client:
        task = asyncio.create_task(client.post(
            '/v1/chat/completions',
            json={'model': 'gemini-2.0-flash', 'messages': [{'role': 'user', 'content': 'hi'}]},
            headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': idempotency_key},
        ))
        
        # Wait until finalize_request has definitely started
        await finalize_started.wait()
        task.cancel()
        
        try:
            await task
        except asyncio.CancelledError:
            pass
            
    # Give the shielded finalize_request time to complete
    await asyncio.sleep(0.6)
    
    # Check the ledger
    stmt = select(UsageLedger).where(UsageLedger.idempotency_key == idempotency_key)
    result = await db_session.execute(stmt)
    row = result.scalar_one_or_none()
    
    assert row is not None
    assert row.status == 'completed'@pytest.mark.asyncio
async def test_idempotency_cancel_during_finalize_leaves_row_completed(client, setup_tenant, db_session, monkeypatch, caplog):
    # This test directly tests the FastAPI endpoint with an asyncio.Task cancellation,
    # so we'll explicitly cancel the request task.
    import asyncio
    from infrgate.db.models.usage_ledger import UsageLedger
    from sqlalchemy import select

    tenant, api_key = setup_tenant
    idempotency_key = 'cancel-finalize-key-123'
    
    import infrgate.api.v1.chat_completions as cc
    original_finalize = cc.finalize_request
    
    finalize_started = asyncio.Event()

    async def mock_finalize(*args, **kwargs):
        finalize_started.set()
        # Sleep to keep it 'in flight' while we cancel the parent task
        await asyncio.sleep(0.5)
        # Call original to actually do the DB update, simulating completion
        await original_finalize(*args, **kwargs)

    monkeypatch.setattr(cc, 'finalize_request', mock_finalize)
    
    task = asyncio.create_task(client.post(
        '/v1/chat/completions',
        json={'model': 'gemini-2.0-flash', 'messages': [{'role': 'user', 'content': 'hi'}]},
        headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': idempotency_key},
    ))
    
    # Wait until finalize_request has definitely started
    await finalize_started.wait()
    task.cancel()
    
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Give the shielded finalize_request time to complete
    await asyncio.sleep(0.6)
    
    # Check the ledger
    stmt = select(UsageLedger).where(UsageLedger.idempotency_key == idempotency_key)
    result = await db_session.execute(stmt)
    row = result.scalar_one_or_none()
    
    assert row is not None
    assert row.status == 'completed'


@pytest.mark.asyncio
async def test_idempotency_cancel_mid_flight_non_streaming_leaves_row_partial(client, setup_tenant, db_session, monkeypatch):
    import asyncio
    from infrgate.db.models.usage_ledger import UsageLedger
    from sqlalchemy import select
    import infrgate.services.reliability as reliability
    import infrgate.api.v1.chat_completions as cc

    tenant, api_key = setup_tenant
    idempotency_key = 'cancel-midflight-ns-key-123'
    
    async def mock_execute(*args, **kwargs):
        # Simulate asyncio.wait_for timeout or client disconnect cancellation
        raise asyncio.CancelledError()

    monkeypatch.setattr(cc, 'execute_with_failover', mock_execute)
    
    # We can just await the call. It will return a 499 or the connection will drop.
    # Actually, httpx.AsyncClient will catch the disconnect if it's unhandled, but our endpoint
    # handles it and re-raises CancelledError! If it re-raises, ASGITransport catches it and drops.
    try:
        response = await client.post(
            '/v1/chat/completions',
            json={'model': 'gemini-2.0-flash', 'messages': [{'role': 'user', 'content': 'hi'}]},
            headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': idempotency_key},
        )
    except Exception:
        pass # Transport might raise an exception when the app raises CancelledError
    
    # Check the ledger
    stmt = select(UsageLedger).where(UsageLedger.idempotency_key == idempotency_key)
    result = await db_session.execute(stmt)
    row = result.scalar_one_or_none()
    
    assert row is not None
    assert row.status == 'partial'


@pytest.mark.asyncio
async def test_idempotency_cancel_mid_flight_streaming_leaves_row_partial(client, setup_tenant, db_session, monkeypatch):
    import asyncio
    from infrgate.db.models.usage_ledger import UsageLedger
    from sqlalchemy import select
    import infrgate.services.reliability as reliability
    import infrgate.api.v1.chat_completions as cc

    tenant, api_key = setup_tenant
    idempotency_key = 'cancel-midflight-s-key-123'
    
    async def mock_execute_stream(*args, **kwargs):
        # Simulate asyncio.wait_for timeout or client disconnect cancellation
        raise asyncio.CancelledError()

    monkeypatch.setattr(cc, 'execute_stream_with_failover', mock_execute_stream)
    
    try:
        response = await client.post(
            '/v1/chat/completions',
            json={'model': 'gemini-2.0-flash', 'messages': [{'role': 'user', 'content': 'hi'}], 'stream': True},
            headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': idempotency_key},
        )
    except Exception:
        pass
    
    # Check the ledger
    stmt = select(UsageLedger).where(UsageLedger.idempotency_key == idempotency_key)
    result = await db_session.execute(stmt)
    row = result.scalar_one_or_none()
    
    assert row is not None
    assert row.status == 'partial'
