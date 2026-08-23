"""
Locust load testing script for InfrGate.
Simulates realistic inference traffic to test routing, failover, and metrics.
"""

from locust import HttpUser, task, between, events
import json
import uuid

# Hardcoded tenant ID/API key for local load testing based on seed data
TENANT_ID = "00000000-0000-0000-0000-000000000001"
API_KEY = "sk-test-123"

class InferenceUser(HttpUser):
    wait_time = between(0.1, 1.0)
    
    def on_start(self):
        """Setup headers for all requests"""
        self.headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
    @task(3)
    def simple_chat_completion(self):
        """Simulate a simple non-streaming chat completion request"""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "user", "content": "Hello, what time is it?"}
            ],
            "stream": False
        }
        
        with self.client.post("/v1/chat/completions", headers=self.headers, json=payload, catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Failed with status: {response.status_code}")

    @task(1)
    def streaming_chat_completion(self):
        """Simulate a streaming chat completion request"""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "user", "content": "Write a short poem about the sea."}
            ],
            "stream": True
        }
        
        # Note: Locust doesn't natively parse SSE, but it will read the stream
        with self.client.post("/v1/chat/completions", headers=self.headers, json=payload, stream=True, catch_response=True) as response:
            if response.status_code == 200:
                # Read chunks
                for line in response.iter_lines():
                    if line:
                        pass
                response.success()
            else:
                response.failure(f"Streaming failed with status: {response.status_code}")
