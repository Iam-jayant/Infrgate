import asyncio
import pytest
from infrgate.services.reliability import get_circuit_state, record_circuit_result, CircuitBreakerConfig, CircuitState

@pytest.mark.asyncio
async def test_circuit_breaker_full_cycle(mock_redis):
    # In order to test the real circuit breaker logic, we need a real Redis or an in-memory substitute.
    # We will use fakeredis if available, or just a mock dict for redis in this test since the logic
    # uses HINCRBY, EXPIRE etc.
    import fakeredis.aioredis
    redis = fakeredis.aioredis.FakeRedis()
    
    provider = "flaky-test"
    cb_config = CircuitBreakerConfig(failure_threshold=3, recovery_timeout_s=1.0, success_threshold=2)

    # Initially CLOSED
    state = await get_circuit_state(redis, provider, cb_config)
    assert state == CircuitState.CLOSED

    # Record 3 failures
    for _ in range(cb_config.failure_threshold):
        await record_circuit_result(redis, provider, success=False, config=cb_config)

    # State should now be OPEN
    state = await get_circuit_state(redis, provider, cb_config)
    assert state == CircuitState.OPEN

    # Wait for recovery timeout
    await asyncio.sleep(1.1)

    # State should now be HALF_OPEN
    state = await get_circuit_state(redis, provider, cb_config)
    assert state == CircuitState.HALF_OPEN

    # Record 1 success (less than success_threshold)
    await record_circuit_result(redis, provider, success=True, config=cb_config)
    
    # State should still be HALF_OPEN
    state = await get_circuit_state(redis, provider, cb_config)
    assert state == CircuitState.HALF_OPEN

    # Record 2nd success
    await record_circuit_result(redis, provider, success=True, config=cb_config)

    # State should return to CLOSED
    state = await get_circuit_state(redis, provider, cb_config)
    assert state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_bypasses_open_provider(mock_redis):
    from infrgate.services.reliability import execute_with_failover, CircuitBreakerConfig, EligibleProvider, TimeoutConfig, RetryPolicy
    from infrgate.providers.base import ProviderRequest
    from unittest.mock import AsyncMock
    import fakeredis.aioredis
    from fastapi import HTTPException
    
    redis = fakeredis.aioredis.FakeRedis()
    provider_name = "test-open-provider"
    cb_config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout_s=30, success_threshold=1)
    
    # Force state to OPEN by recording failures
    await record_circuit_result(redis, provider_name, success=False, config=cb_config)
    
    # Assert it is OPEN
    state = await get_circuit_state(redis, provider_name, cb_config)
    assert state == CircuitState.OPEN

    # Mock the adapter
    mock_adapter = AsyncMock()
    mock_adapter.provider_name = provider_name
    
    eligible = EligibleProvider(
        adapter=mock_adapter,
        config=None,
        timeout_config=TimeoutConfig(),
        retry_policy=RetryPolicy()
    )
    
    request = ProviderRequest(
        request_id="test-req",
        model="test-model",
        messages=[{"role": "user", "content": "hello"}]
    )
    
    # execute_with_failover should skip this provider and raise 503 since no other providers are available
    with pytest.raises(HTTPException) as exc_info:
        await execute_with_failover([eligible], request, redis, cb_config)
        
    assert exc_info.value.status_code == 503
    details = exc_info.value.detail["error"]["details"]
    assert any("circuit open" in d for d in details)
    
    # The provider adapter should NOT have been called
    mock_adapter.complete.assert_not_called()
    mock_adapter.stream.assert_not_called()
