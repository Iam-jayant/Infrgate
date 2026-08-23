import pytest
from unittest.mock import AsyncMock
from infrgate.services.scoring import ewma_update, record_health_signal, get_health_signals, calculate_score, HealthSignals, RoutingWeights
from infrgate.services.reliability import EligibleProvider, TimeoutConfig, RetryPolicy
from infrgate.db.models.provider_config import ProviderConfig

def test_ewma_update():
    # Initial state 100, new sample 200, alpha 0.3
    # result = 0.3 * 200 + 0.7 * 100 = 60 + 70 = 130
    assert ewma_update(100, 200, 0.3) == 130.0

@pytest.mark.asyncio
async def test_record_and_get_health_signal():
    redis = AsyncMock()
    
    # Setup mock returns
    redis.hgetall.side_effect = [
        {}, # record_health_signal 1
        {}, # get_health_signals 1 (circuit)
        {b"ewma_latency_ms": b"100.0", b"ewma_error_rate": b"0.0"}, # get_health_signals 1 (health)
        {b"ewma_latency_ms": b"100.0", b"ewma_error_rate": b"0.0"}, # record_health_signal 2
        {}, # get_health_signals 2 (circuit)
        {b"ewma_latency_ms": b"150.0", b"ewma_error_rate": b"0.5"}, # get_health_signals 2 (health)
    ]
    
    await record_health_signal(redis, "test_provider", 100, False, alpha=1.0)
    assert redis.hset.called
    
    signals = await get_health_signals(redis, "test_provider")
    assert signals.ewma_latency_ms == 100.0
    assert signals.ewma_error_rate == 0.0

    await record_health_signal(redis, "test_provider", 200, True, alpha=0.5)
    
    signals = await get_health_signals(redis, "test_provider")
    assert signals.ewma_latency_ms == 150.0
    assert signals.ewma_error_rate == 0.5

def test_calculate_score():
    class MockAdapter:
        provider_name = "test"
        supported_models = ["test-model"]
        
    config = ProviderConfig(
        provider_name="test",
        cost_per_1k_tokens={"test-model": {"prompt": 2.0}}
    )
    
    provider = EligibleProvider(
        adapter=MockAdapter(),
        config=config,
        timeout_config=TimeoutConfig(),
        retry_policy=RetryPolicy()
    )
    
    health = HealthSignals(availability=1.0, ewma_error_rate=0.1, ewma_latency_ms=1000.0)
    weights = RoutingWeights(availability=0.4, error_rate=0.3, latency=0.2, cost=0.1)
    
    score = calculate_score(provider, "test-model", health, weights)
    
    expected = (0.4 * 1.0) + (0.3 * 0.9) + (0.2 * 0.8) + (0.1 * 0.8)
    
    assert abs(score - 0.91) < 0.0001
