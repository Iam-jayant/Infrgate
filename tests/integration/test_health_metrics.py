import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_liveness(client: AsyncClient):
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

@pytest.mark.asyncio
async def test_readiness(client: AsyncClient):
    # In a test environment, both redis and postgres should be up
    response = await client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"]["postgres"] == "ok"
    assert data["checks"]["redis"] == "ok"
    # Should show providers
    assert "gemini" in data["checks"]["providers"]

@pytest.mark.asyncio
async def test_metrics(client: AsyncClient):
    # Hit health endpoint to ensure some activity
    await client.get("/health/live")
    
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    
    # We should see our defined metrics in the output
    content = response.text
    assert "infrgate_active_requests" in content
    assert "infrgate_provider_circuit_state" in content
