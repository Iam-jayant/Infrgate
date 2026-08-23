import pytest
import time
from infrgate.schemas.streaming import StreamChunk, StreamUsageTracker

def test_stream_chunk_to_sse():
    chunk = StreamChunk(
        id="test1",
        model="fake-model",
        delta_role="assistant"
    )
    sse = chunk.to_sse_event()
    assert sse.startswith("data: ")
    assert "fake-model" in sse
    assert "assistant" in sse

def test_stream_usage_tracker():
    tracker = StreamUsageTracker()
    tracker.update(StreamChunk(id="1", model="m", delta_role="assistant"))
    tracker.update(StreamChunk(id="1", model="m", delta_content="hello"))
    tracker.update(StreamChunk(id="1", model="m", delta_content=" world"))
    tracker.update(StreamChunk(id="1", model="m", finish_reason="stop"))
    
    res = tracker.finalize()
    assert res["prompt_tokens"] == 0
    assert res["completion_tokens"] > 0
    assert res["finish_reason"] == "stop"
    assert res["status"] == "completed"

def test_stream_usage_tracker_provider_reported():
    tracker = StreamUsageTracker()
    tracker.update(StreamChunk(id="1", model="m", usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}))
    
    res = tracker.finalize()
    assert res["prompt_tokens"] == 10
    assert res["completion_tokens"] == 20
    assert res["total_tokens"] == 30
