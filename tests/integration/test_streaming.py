import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_streaming_endpoint(client: AsyncClient, test_api_key):
    _, api_key = test_api_key
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": "gemini-2.0-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True
        }
    )
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/event-stream")
    
    # Depending on how the client is configured, we may need to read the response.
    # In httpx test client, for streaming responses, we can use `aiter_lines()` or just read it.
    
    # This might be tricky if it's already read, but let's assume simple read works
    content = ""
    async for chunk in response.aiter_bytes():
        content += chunk.decode()
        
    assert "data: " in content
    assert "data: [DONE]" in content

@pytest.mark.asyncio
async def test_streaming_mid_stream_timeout(client: AsyncClient, test_api_key):
    from unittest.mock import patch, AsyncMock
    from infrgate.services.reliability import EligibleProvider, TimeoutConfig, RetryPolicy
    
    _, api_key = test_api_key
    
    async def get_slow_stream(request):
        from infrgate.schemas.streaming import StreamChunk
        import asyncio
        yield StreamChunk(id="1", model="gemini-2.0-flash", delta_role="assistant")
        await asyncio.sleep(0.5)
        yield StreamChunk(id="1", model="gemini-2.0-flash", delta_content="hello")

    mock_adapter = AsyncMock()
    mock_adapter.provider_name = "gemini"
    mock_adapter.stream = get_slow_stream

    ep = EligibleProvider(
        adapter=mock_adapter,
        config=None,
        timeout_config=TimeoutConfig(total_timeout_s=5.0, stream_read_timeout_s=0.1),
        retry_policy=RetryPolicy()
    )

    with patch("infrgate.api.v1.chat_completions.resolve_providers", return_value=[ep]):
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True
            }
        )
        assert response.status_code == 200
        
        content = ""
        async for chunk in response.aiter_bytes():
            content += chunk.decode()
            
        assert "provider_timeout" in content
        assert "data: [DONE]" in content
