import pytest
import json

@pytest.mark.asyncio
async def test_get_routing_decisions(client, mock_redis):
    # Setup mock redis data
    mock_redis.lrange.return_value = [
        json.dumps({
            "request_id": "test-req-1",
            "requested_model": "gpt-4o",
            "eligible_providers": ["openai", "gemini"],
            "selected_provider": "openai",
            "fallback_used": False,
            "reason": "primary",
            "scores": {"openai": {"total": 0.9}},
            "timestamp": "2026-08-21T10:00:00Z",
            "tenant_id": "tenant-1"
        })
    ]
    
    headers = {"Authorization": "Bearer test-admin-key"}
    response = await client.get("/admin/routing/decisions", headers=headers)
    
    assert response.status_code == 200
    data = response.json()
    assert "decisions" in data
    assert len(data["decisions"]) == 1
    assert data["decisions"][0]["request_id"] == "test-req-1"
    
    # Test filtering
    response = await client.get("/admin/routing/decisions?tenant_id=tenant-2", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["decisions"]) == 0
